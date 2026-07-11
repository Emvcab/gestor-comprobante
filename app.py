"""
app.py
-------
Gestor Automático de Comprobantes — aplicación web (Streamlit) para que
comerciantes sin conocimientos técnicos suban fotos/capturas de
comprobantes de transferencia de Mercado Pago y obtengan un Excel/CSV
listo para usar, sin cargar datos a mano.

Reutiliza el mismo pipeline ya probado en el CLI (preprocessing.py,
ocr_engine.py, extractor.py) — esta capa solo se encarga de la interfaz,
el manejo de errores "amigable" y la edición humana de los resultados
antes de exportar.

Ejecutar localmente:
    streamlit run app.py
"""

import io
from datetime import datetime

import cv2
import numpy as np
import pandas as pd
import streamlit as st

from extractor import DataExtractor
from ocr_engine import TesseractOCREngine
from preprocessing import ImagePreprocessor

# --------------------------------------------------------------------------
# Configuración de la página. DEBE ser el primer comando de Streamlit que
# se ejecuta en el script.
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="Gestor Automático de Comprobantes",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Columnas que se muestran/editan en la tabla, en el orden final del export.
COLUMNAS = [
    "archivo", "monto", "fecha_hora", "motivo",
    "emisor", "emisor_cuit_cuil", "emisor_cvu",
    "receptor", "receptor_cuit_cuil", "receptor_cvu",
    "numero_operacion", "estado", "detalle_error",
]

# Columnas que contienen cadenas largas de dígitos (CVU, número de
# operación) o que mezclan dígitos con guiones (CUIT/CUIL). Si Excel las
# interpreta como número, las muestra en notación científica y pierde
# precisión — por eso se fuerzan siempre como texto al exportar.
COLUMNAS_TEXTO_NUMERICO = [
    "numero_operacion", "emisor_cuit_cuil", "emisor_cvu",
    "receptor_cuit_cuil", "receptor_cvu",
]

# Columnas que NO tiene sentido que el comerciante edite a mano (son
# metadatos del procesamiento, no datos del comprobante en sí).
COLUMNAS_NO_EDITABLES = ["archivo", "estado", "detalle_error"]


# --------------------------------------------------------------------------
# Inicialización de los módulos del pipeline. Son livianos (no cargan
# modelos pesados como haría EasyOCR), así que no hace falta cachearlos
# con @st.cache_resource, pero se instancian una sola vez por sesión.
# --------------------------------------------------------------------------
@st.cache_resource
def get_pipeline_modules():
    """Crea las instancias de preprocesador, motor OCR y extractor.
    Cacheado por sesión de Streamlit para no recrearlas en cada rerun."""
    preprocessor = ImagePreprocessor(debug=False)
    ocr_engine = TesseractOCREngine(lang="spa")
    extractor = DataExtractor()
    return preprocessor, ocr_engine, extractor


def process_uploaded_file(uploaded_file, preprocessor, ocr_engine, extractor) -> dict:
    """
    Procesa un único archivo subido por el usuario: decodifica la imagen,
    la preprocesa, corre el OCR y extrae los campos estructurados.

    Manejo de errores "silencioso": pase lo que pase (imagen corrupta,
    que no sea un comprobante, OCR ilegible, campo faltante), esta función
    SIEMPRE devuelve un diccionario válido con un 'estado' describiendo
    qué pasó, en vez de lanzar una excepción que tumbaría la app.
    """
    result = {col: None for col in COLUMNAS}
    result["archivo"] = uploaded_file.name
    result["estado"] = "OK"
    result["detalle_error"] = ""

    # --- Decodificación de la imagen subida (bytes -> array de OpenCV) ---
    try:
        file_bytes = np.frombuffer(uploaded_file.getvalue(), dtype=np.uint8)
        image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("el archivo no parece ser una imagen válida")
    except Exception as e:
        result["estado"] = "ERROR"
        result["detalle_error"] = f"No se pudo leer la imagen ({e})"
        return result

    # --- Preprocesamiento (OpenCV) ---
    try:
        debug_name = uploaded_file.name.rsplit(".", 1)[0]
        processed_image = preprocessor.process_image(image, debug_name=debug_name)
    except Exception as e:
        result["estado"] = "ERROR"
        result["detalle_error"] = f"Fallo al preprocesar la imagen ({e})"
        return result

    # --- OCR ---
    try:
        raw_text = ocr_engine.extract_text(processed_image)
    except Exception as e:
        result["estado"] = "ERROR"
        result["detalle_error"] = f"Fallo del motor OCR ({e})"
        return result

    if not raw_text or not raw_text.strip():
        result["estado"] = "ERROR"
        result["detalle_error"] = "No se detectó texto legible. ¿Es una imagen de un comprobante?"
        return result

    # --- Extracción de campos ---
    campos_faltantes = []
    for campo, metodo in [
        ("monto", extractor.extract_monto),
        ("fecha_hora", extractor.extract_fecha_hora),
        ("motivo", extractor.extract_motivo),
        ("numero_operacion", extractor.extract_numero_operacion),
    ]:
        try:
            valor = metodo(raw_text)
            result[campo] = valor
            if valor is None:
                campos_faltantes.append(campo)
        except Exception:
            campos_faltantes.append(campo)

    try:
        datos_personas = extractor.extract_emisor_receptor(raw_text)
        result.update(datos_personas)
        campos_faltantes.extend([k for k, v in datos_personas.items() if v is None])
    except Exception:
        campos_faltantes.extend([
            "emisor", "emisor_cuit_cuil", "emisor_cvu",
            "receptor", "receptor_cuit_cuil", "receptor_cvu",
        ])

    if campos_faltantes:
        result["estado"] = "REVISAR"
        result["detalle_error"] = f"No se detectaron: {', '.join(campos_faltantes)}. Completalo a mano en la tabla."

    return result


def build_excel_bytes(df: pd.DataFrame) -> bytes:
    """Convierte el DataFrame final (ya editado por el usuario) a un
    archivo Excel en memoria, forzando texto en las columnas numéricas
    largas para evitar notación científica."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Comprobantes")
        worksheet = writer.sheets["Comprobantes"]
        for col in COLUMNAS_TEXTO_NUMERICO:
            if col in df.columns:
                col_idx = df.columns.get_loc(col) + 1  # openpyxl es 1-indexed
                for row in range(2, len(df) + 2):
                    worksheet.cell(row=row, column=col_idx).number_format = "@"
    buffer.seek(0)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Estado de sesión: guarda los resultados procesados entre reruns, para
# que editar una celda en la tabla no dispare de nuevo todo el OCR.
# --------------------------------------------------------------------------
if "df_resultados" not in st.session_state:
    st.session_state.df_resultados = None
if "editor_run_id" not in st.session_state:
    st.session_state.editor_run_id = 0


# ==========================================================================
# INTERFAZ
# ==========================================================================

st.title("🧾 Gestor Automático de Comprobantes")
st.caption(
    "Subí las capturas de tus comprobantes de Mercado Pago y obtené un "
    "Excel listo para usar — sin cargar nada a mano."
)

with st.expander("ℹ️ ¿Cómo funciona?", expanded=False):
    st.markdown(
        """
        1. **Subí** una o varias capturas de pantalla de comprobantes (PNG o JPG).
        2. Hacé clic en **Procesar comprobantes**. El sistema lee cada imagen automáticamente.
        3. **Revisá la tabla**: si algún dato no se leyó bien, hacé doble clic en la celda y corregilo a mano.
        4. **Descargá** el archivo Excel o CSV con todos los datos ya organizados.
        """
    )

st.divider()

# --- 1. Subida de archivos ---
uploaded_files = st.file_uploader(
    "Subí tus comprobantes",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
    help="Podés seleccionar varias imágenes a la vez.",
)

col_btn, col_info = st.columns([1, 3])
with col_btn:
    procesar = st.button(
        "🚀 Procesar comprobantes",
        type="primary",
        disabled=not uploaded_files,
        use_container_width=True,
    )
with col_info:
    if uploaded_files:
        st.write(f"📎 {len(uploaded_files)} imagen(es) lista(s) para procesar.")

# --- 2. Feedback visual + 3. Manejo de errores silencioso ---
if procesar and uploaded_files:
    preprocessor, ocr_engine, extractor = get_pipeline_modules()

    resultados = []
    total = len(uploaded_files)
    progress_bar = st.progress(0, text="Iniciando...")

    for idx, uploaded_file in enumerate(uploaded_files, start=1):
        progress_bar.progress(
            (idx - 1) / total,
            text=f"Procesando {idx}/{total}: {uploaded_file.name}",
        )
        resultado = process_uploaded_file(uploaded_file, preprocessor, ocr_engine, extractor)
        resultados.append(resultado)

    progress_bar.progress(1.0, text="¡Listo!")
    progress_bar.empty()

    df = pd.DataFrame(resultados)[COLUMNAS]
    # Fuerza texto en columnas numéricas largas ya en esta instancia,
    # para que el data_editor tampoco las muestre en notación científica.
    for col in COLUMNAS_TEXTO_NUMERICO:
        df[col] = df[col].apply(lambda x: str(x) if pd.notna(x) else None)

    st.session_state.df_resultados = df
    st.session_state.editor_run_id += 1  # fuerza un editor "limpio" para esta tanda

    # Resumen de resultados
    n_ok = (df["estado"] == "OK").sum()
    n_revisar = (df["estado"] == "REVISAR").sum()
    n_error = (df["estado"] == "ERROR").sum()

    if n_error > 0:
        archivos_con_error = df.loc[df["estado"] == "ERROR", "archivo"].tolist()
        st.warning(
            f"⚠️ {n_error} imagen(es) no se pudieron procesar: "
            f"{', '.join(archivos_con_error)}. Revisá que sean capturas legibles "
            f"de un comprobante y volvé a intentar con esas."
        )
    if n_revisar > 0:
        st.info(f"✏️ {n_revisar} comprobante(s) necesitan revisión manual (algún campo no se detectó).")
    if n_ok == total:
        st.success(f"✅ Los {total} comprobantes se procesaron correctamente.")

# --- 4. Tabla de revisión (human-in-the-loop) ---
if st.session_state.df_resultados is not None:
    st.divider()
    st.subheader("📋 Revisá y corregí los datos")
    st.caption(
        "Hacé doble clic en cualquier celda para editarla. Los cambios se "
        "reflejan automáticamente en el archivo que descargues abajo."
    )

    column_config = {
        "archivo": st.column_config.TextColumn("Archivo", width="medium"),
        "monto": st.column_config.TextColumn("Monto", width="small"),
        "fecha_hora": st.column_config.TextColumn("Fecha y hora", width="medium"),
        "motivo": st.column_config.TextColumn("Motivo", width="small"),
        "emisor": st.column_config.TextColumn("Emisor (envía)", width="medium"),
        "emisor_cuit_cuil": st.column_config.TextColumn("CUIT/CUIL emisor", width="medium"),
        "emisor_cvu": st.column_config.TextColumn("CVU emisor", width="large"),
        "receptor": st.column_config.TextColumn("Receptor (recibe)", width="medium"),
        "receptor_cuit_cuil": st.column_config.TextColumn("CUIT/CUIL receptor", width="medium"),
        "receptor_cvu": st.column_config.TextColumn("CVU receptor", width="large"),
        "numero_operacion": st.column_config.TextColumn("N° de operación", width="medium"),
        "estado": st.column_config.TextColumn("Estado", width="small"),
        "detalle_error": st.column_config.TextColumn("Detalle", width="large"),
    }

    edited_df = st.data_editor(
        st.session_state.df_resultados,
        key=f"editor_{st.session_state.editor_run_id}",
        column_config=column_config,
        disabled=COLUMNAS_NO_EDITABLES,
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
    )

    # --- 5. Descarga de datos ---
    st.divider()
    st.subheader("⬇️ Descargar")

    nombre_base = f"comprobantes_{datetime.now().strftime('%Y%m%d_%H%M')}"

    col_xlsx, col_csv = st.columns(2)
    with col_xlsx:
        st.download_button(
            label="📥 Descargar Excel (.xlsx)",
            data=build_excel_bytes(edited_df),
            file_name=f"{nombre_base}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with col_csv:
        st.download_button(
            label="📥 Descargar CSV",
            data=edited_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{nombre_base}.csv",
            mime="text/csv",
            use_container_width=True,
        )
else:
    st.info("👆 Subí una o más imágenes y presioná **Procesar comprobantes** para empezar.")

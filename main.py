#!/usr/bin/env python3
"""
main.py
--------
CLI para automatizar la extracción de datos de comprobantes de transferencia
de Mercado Pago (Argentina) a partir de imágenes (PNG/JPG), y consolidarlos
en un archivo Excel.

Uso básico:
    python main.py --input_folder ./comprobantes --output resultados.xlsx

Uso con EasyOCR (para fotos de comprobantes en vez de capturas de pantalla):
    python main.py --input_folder ./comprobantes --ocr_engine easyocr

Uso con modo debug (guarda las imágenes preprocesadas para inspección):
    python main.py --input_folder ./comprobantes --debug
"""

import argparse
import logging
import os
import sys

import pandas as pd

from extractor import DataExtractor
from ocr_engine import EasyOCREngine, TesseractOCREngine
from preprocessing import ImagePreprocessor

# --------------------------------------------------------------------------
# Logging: muestra en consola el progreso y los errores, sin detener
# la ejecución del script ante una imagen problemática.
# --------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

VALID_EXTENSIONS = (".png", ".jpg", ".jpeg")


def get_image_files(folder: str) -> list:
    """Devuelve la lista ordenada de rutas de imágenes válidas dentro de una carpeta."""
    if not os.path.isdir(folder):
        raise NotADirectoryError(f"La carpeta de entrada no existe: {folder}")

    return [
        os.path.join(folder, f)
        for f in sorted(os.listdir(folder))
        if f.lower().endswith(VALID_EXTENSIONS)
    ]


def process_single_image(image_path: str, preprocessor: ImagePreprocessor,
                          ocr_engine, extractor: DataExtractor) -> dict:
    """
    Procesa una única imagen: preprocesa -> OCR -> extrae todos los campos
    del comprobante (monto, fecha/hora, motivo, emisor, receptor, CUIT/CUIL
    y CVU de ambos, y número de operación).
    Nunca lanza una excepción hacia afuera: cualquier error queda
    registrado en el diccionario de resultado (columnas 'estado' y
    'detalle_error'), para que el batch completo no se detenga.
    """
    result = {
        "archivo": os.path.basename(image_path),
        "monto": None,
        "fecha_hora": None,
        "motivo": None,
        "emisor": None,
        "emisor_cuit_cuil": None,
        "emisor_cvu": None,
        "receptor": None,
        "receptor_cuit_cuil": None,
        "receptor_cvu": None,
        "numero_operacion": None,
        "estado": "OK",
        "detalle_error": "",
    }

    # --- Preprocesamiento ---
    try:
        processed_image = preprocessor.process(image_path)
    except Exception as e:
        logger.error(f"Fallo en preprocesamiento de {image_path}: {e}")
        result["estado"] = "ERROR_PREPROCESAMIENTO"
        result["detalle_error"] = str(e)
        return result

    # --- OCR ---
    try:
        raw_text = ocr_engine.extract_text(processed_image)
    except Exception as e:
        logger.error(f"Fallo en OCR de {image_path}: {e}")
        result["estado"] = "ERROR_OCR"
        result["detalle_error"] = str(e)
        return result

    if not raw_text or not raw_text.strip():
        logger.warning(f"OCR no devolvió texto para {image_path}")
        result["estado"] = "SIN_TEXTO"
        result["detalle_error"] = "El OCR no pudo leer texto en la imagen."
        return result

    # --- Extracción de campos simples (cada uno independiente del resto) ---
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
        except Exception as e:
            logger.warning(f"Error extrayendo '{campo}' en {image_path}: {e}")
            campos_faltantes.append(campo)

    # --- Extracción de emisor/receptor (bloques "De" / "Para") ---
    try:
        datos_personas = extractor.extract_emisor_receptor(raw_text)
        result.update(datos_personas)
        for campo, valor in datos_personas.items():
            if valor is None:
                campos_faltantes.append(campo)
    except Exception as e:
        logger.warning(f"Error extrayendo emisor/receptor en {image_path}: {e}")
        campos_faltantes.extend([
            "emisor", "emisor_cuit_cuil", "emisor_cvu",
            "receptor", "receptor_cuit_cuil", "receptor_cvu",
        ])

    if campos_faltantes:
        result["estado"] = "PARCIAL"
        result["detalle_error"] = f"No se detectaron: {', '.join(campos_faltantes)}"

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Extrae datos de comprobantes de Mercado Pago (imágenes) y los exporta a Excel."
    )
    parser.add_argument(
        "--input_folder", required=True,
        help="Carpeta que contiene las imágenes de los comprobantes (PNG/JPG).",
    )
    parser.add_argument(
        "--output", default="comprobantes.xlsx",
        help="Ruta del archivo Excel de salida (default: comprobantes.xlsx).",
    )
    parser.add_argument(
        "--ocr_engine", choices=["tesseract", "easyocr"], default="tesseract",
        help="Motor OCR a utilizar (default: tesseract, recomendado para capturas de pantalla).",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Guarda las imágenes preprocesadas en ./debug_images/ para inspección visual.",
    )
    args = parser.parse_args()

    # --- Inicialización de módulos ---
    preprocessor = ImagePreprocessor(debug=args.debug)
    extractor = DataExtractor()

    if args.ocr_engine == "easyocr":
        logger.info("Inicializando EasyOCR (puede tardar unos segundos la primera vez)...")
        ocr_engine = EasyOCREngine(lang_list=["es"])
    else:
        ocr_engine = TesseractOCREngine(lang="spa")

    # --- Búsqueda de imágenes ---
    try:
        image_files = get_image_files(args.input_folder)
    except NotADirectoryError as e:
        logger.error(e)
        sys.exit(1)

    if not image_files:
        logger.warning(f"No se encontraron imágenes (.png/.jpg/.jpeg) en {args.input_folder}")
        sys.exit(0)

    logger.info(f"Se encontraron {len(image_files)} imágenes. Iniciando procesamiento...")

    # --- Procesamiento en lote ---
    resultados = []
    for idx, image_path in enumerate(image_files, start=1):
        logger.info(f"[{idx}/{len(image_files)}] Procesando: {os.path.basename(image_path)}")
        resultados.append(process_single_image(image_path, preprocessor, ocr_engine, extractor))

    # --- Armado del DataFrame y exportación a Excel ---
    df = pd.DataFrame(resultados)
    columnas_ordenadas = [
        "archivo", "monto", "fecha_hora", "motivo",
        "emisor", "emisor_cuit_cuil", "emisor_cvu",
        "receptor", "receptor_cuit_cuil", "receptor_cvu",
        "numero_operacion", "estado", "detalle_error",
    ]
    df = df[columnas_ordenadas]

    # Varios campos son cadenas largas de dígitos (número de operación,
    # CVU) o contienen guiones (CUIT/CUIL). Si se guardan como número,
    # Excel puede mostrarlos en notación científica y perder precisión.
    # Se fuerzan explícitamente como texto (dtype object / string).
    COLUMNAS_TEXTO_NUMERICO = [
        "numero_operacion", "emisor_cuit_cuil", "emisor_cvu",
        "receptor_cuit_cuil", "receptor_cvu",
    ]
    for col in COLUMNAS_TEXTO_NUMERICO:
        df[col] = df[col].apply(lambda x: str(x) if pd.notna(x) else None)

    try:
        with pd.ExcelWriter(args.output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Comprobantes")
            # Fuerza el formato de celda "Texto" en esas columnas para que
            # Excel no las reinterprete como número.
            worksheet = writer.sheets["Comprobantes"]
            for col in COLUMNAS_TEXTO_NUMERICO:
                col_idx = df.columns.get_loc(col) + 1  # 1-indexed
                for row in range(2, len(df) + 2):
                    worksheet.cell(row=row, column=col_idx).number_format = "@"
        logger.info(f"Archivo Excel generado exitosamente: {args.output}")
    except Exception as e:
        logger.error(f"No se pudo guardar el archivo Excel: {e}")
        sys.exit(1)

    # --- Resumen final ---
    ok_count = (df["estado"] == "OK").sum()
    parcial_count = (df["estado"] == "PARCIAL").sum()
    error_count = len(df) - ok_count - parcial_count

    logger.info("----- RESUMEN -----")
    logger.info(f"Total procesadas: {len(df)}")
    logger.info(f"OK (todos los datos): {ok_count}")
    logger.info(f"Parciales (faltan algunos datos): {parcial_count}")
    logger.info(f"Con error / sin texto legible: {error_count}")


if __name__ == "__main__":
    main()

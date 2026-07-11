"""
Módulo Motor OCR
-----------------
Se eligió pytesseract (wrapper de Tesseract OCR) como motor PRINCIPAL porque:

  - Los comprobantes de Mercado Pago son capturas de pantalla digitales
    (texto limpio, tipografía uniforme, sin ángulos ni distorsión de
    cámara). En este escenario Tesseract funciona muy bien.
  - Tesseract es mucho más liviano y rápido que EasyOCR (que usa redes
    neuronales pesadas pensadas para texto "en la naturaleza": fotos de
    carteles, escenas con ángulos, etc.) y no requiere GPU.
  - Tiene soporte nativo y maduro para español ('spa').
  - Es ideal para procesamiento en lote de muchas imágenes.

Se deja también una clase EasyOCREngine por si en el futuro se necesita
procesar fotos de comprobantes impresos, con mala iluminación o ángulo,
donde EasyOCR suele ser más robusto.
"""

import pytesseract


class TesseractOCREngine:
    """Motor OCR basado en pytesseract, configurado para español."""

    def __init__(self, lang: str = "spa"):
        self.lang = lang
        # PSM 3 = "Fully automatic page segmentation" (default de Tesseract).
        # Se probó también PSM 6 ("single uniform block of text"), pero
        # falla al leer el monto: en los comprobantes reales el monto está
        # en una tipografía mucho más grande/negrita que el resto, y PSM 6
        # asume un bloque uniforme, por lo que descarta esa línea. PSM 3
        # analiza el layout de forma automática y sí la detecta.
        self.config = "--psm 3"

    def extract_text(self, processed_image) -> str:
        """Recibe una imagen (numpy array) ya preprocesada y devuelve
        el texto crudo extraído."""
        text = pytesseract.image_to_string(
            processed_image, lang=self.lang, config=self.config
        )
        return text


class EasyOCREngine:
    """
    Motor OCR alternativo basado en EasyOCR (deep learning).
    Requiere: pip install easyocr
    Se recomienda solo si las imágenes son fotos (no capturas de pantalla)
    o tienen mala calidad/ángulo.
    """

    def __init__(self, lang_list=None):
        import easyocr  # import diferido: no obliga a instalar la librería
        # si el usuario nunca usa este motor.
        self.reader = easyocr.Reader(lang_list or ["es"], gpu=False)

    def extract_text(self, processed_image) -> str:
        results = self.reader.readtext(processed_image, detail=0, paragraph=True)
        return "\n".join(results)

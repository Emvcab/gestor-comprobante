"""
Módulo de Preprocesamiento de Imagen
--------------------------------------
Utiliza OpenCV para preparar las imágenes de comprobantes antes del OCR.
El objetivo es maximizar la precisión del motor de reconocimiento de texto,
aplicando técnicas estándar de visión por computadora:

  1. Escalado (upscale) si la imagen es pequeña (celulares suelen recortar
     capturas a resoluciones bajas).
  2. Conversión a escala de grises.
  3. Mejora de contraste con CLAHE (útil en capturas con fondos de color,
     como el celeste/azul típico de Mercado Pago).
  4. Reducción de ruido.
  5. Binarización (thresholding) con el método de Otsu, que calcula
     automáticamente el umbral óptimo blanco/negro.
"""

import os

import cv2
import numpy as np


class ImagePreprocessor:
    """Encapsula todas las operaciones de preprocesamiento de imagen."""

    def __init__(self, upscale_factor: float = 1.5, debug: bool = False):
        """
        Args:
            upscale_factor: factor de escalado para imágenes pequeñas.
            debug: si es True, guarda cada imagen procesada en ./debug_images/
                   para poder revisar visualmente por qué el OCR falla.
        """
        self.upscale_factor = upscale_factor
        self.debug = debug
        if self.debug:
            os.makedirs("debug_images", exist_ok=True)

    def load_image(self, image_path: str) -> np.ndarray:
        """Carga una imagen desde disco. Lanza ValueError si no es legible
        (archivo corrupto, formato no soportado, etc.)."""
        image = cv2.imread(image_path)
        if image is None:
            raise ValueError(f"No se pudo leer la imagen (¿archivo corrupto o formato inválido?): {image_path}")
        return image

    def upscale(self, image: np.ndarray) -> np.ndarray:
        """Escala la imagen si es angosta. Tesseract mejora notablemente
        su precisión con texto de mayor resolución."""
        height, width = image.shape[:2]
        if width < 1000:
            new_size = (int(width * self.upscale_factor), int(height * self.upscale_factor))
            image = cv2.resize(image, new_size, interpolation=cv2.INTER_CUBIC)
        return image

    def to_grayscale(self, image: np.ndarray) -> np.ndarray:
        """Convierte la imagen a escala de grises."""
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def improve_contrast(self, image: np.ndarray) -> np.ndarray:
        """Aplica CLAHE para resaltar el texto sobre fondos con poco
        contraste (frecuente en capturas de pantalla con colores de marca)."""
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return clahe.apply(image)

    def denoise(self, image: np.ndarray) -> np.ndarray:
        """Elimina ruido conservando los bordes del texto."""
        return cv2.fastNlMeansDenoising(image, h=10)

    def binarize(self, image: np.ndarray) -> np.ndarray:
        """Binariza la imagen (blanco/negro puro) usando Otsu, que elige
        automáticamente el mejor umbral según el histograma de la imagen."""
        _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary

    def process(self, image_path: str) -> np.ndarray:
        """Pipeline completo desde un archivo en disco: carga -> escala ->
        gris -> contraste -> ruido -> binarización. Usado por el CLI
        (main.py). Devuelve la imagen lista para el OCR."""
        image = self.load_image(image_path)
        base_name = os.path.splitext(os.path.basename(image_path))[0]
        return self.process_image(image, debug_name=base_name)

    def process_image(self, image: np.ndarray, debug_name: str = "imagen") -> np.ndarray:
        """
        Mismo pipeline que `process()`, pero recibe una imagen YA CARGADA
        en memoria (numpy array) en vez de una ruta de archivo. Esto permite
        reusar el preprocesamiento desde la app web (Streamlit), donde las
        imágenes llegan como bytes subidos por el navegador y se decodifican
        con cv2.imdecode antes de llegar acá — sin necesidad de guardarlas
        en disco primero.
        """
        image = self.upscale(image)
        gray = self.to_grayscale(image)
        gray = self.improve_contrast(gray)
        gray = self.denoise(gray)
        binary = self.binarize(gray)

        if self.debug:
            cv2.imwrite(f"debug_images/{debug_name}_processed.png", binary)

        return binary

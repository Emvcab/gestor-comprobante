"""
Módulo de Extracción de Datos (Regex)
---------------------------------------
Contiene la lógica para extraer los campos estructurados a partir del
texto crudo devuelto por el OCR.

El comprobante real de Mercado Pago (transferencia) tiene una estructura
con dos bloques bien definidos:

    De                          <- datos del EMISOR (quien envía)
    Nombre Completo
    CUIT/CUIL: XX-XXXXXXXX-X
    Mercado Pago
    CVU: XXXXXXXXXXXXXXXXXXXXXX

    Para                        <- datos del RECEPTOR (quien recibe)
    Nombre Completo
    CUIT/CUIL: XX-XXXXXXXX-X
    Mercado Pago
    CVU: XXXXXXXXXXXXXXXXXXXXXX

Por eso, en vez de adivinar un único patrón de "nombre", el extractor
primero separa el texto en el bloque "De" y el bloque "Para" (usando las
palabras clave como delimitadores), y dentro de cada bloque busca el
nombre, el CUIT/CUIL y el CVU correspondientes. Esto evita el error común
de confundir al emisor con el receptor.

El OCR puede introducir ruido menor (viñetas mal leídas como "*", "e" o
"%", saltos de línea extra), por lo que la separación en bloques y la
limpieza de texto están pensadas para tolerar esos errores típicos.
"""

import re

# Meses en español, usados para parsear fechas tipo "10 de julio de 2026"
MESES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "setiembre": "09", "octubre": "10",
    "noviembre": "11", "diciembre": "12",
}

# Etiquetas que NO deben confundirse con un nombre de persona dentro de
# un bloque "De"/"Para" (ruido típico de OCR o líneas de metadata)
ETIQUETAS_NO_NOMBRE = ("cuit", "cvu", "cbu", "alias", "mercado", "pago")


class DataExtractor:
    """Extrae los campos de un comprobante de transferencia de Mercado Pago
    a partir del texto crudo devuelto por el OCR."""

    # ------------------------------------------------------------------
    # MONTO TRANSFERIDO
    # ------------------------------------------------------------------
    MONTO_PATTERNS = [
        r"\$\s*(\d{1,3}(?:\.\d{3})*,\d{2})",   # "$ 15.000,00"
        r"\$\s*(\d{1,3}(?:\.\d{3})+)",         # "$ 6.000" (sin centavos)
        r"\$\s*(\d+,\d{2})",                   # "$ 15000,00" (sin puntos de miles)
        r"\$\s*(\d{3,})",                      # "$ 15000" simple
    ]

    def extract_monto(self, text: str):
        """Busca el monto transferido y lo devuelve con el símbolo $
        incluido (ej: "$6.000"). Devuelve None si no hay match."""
        for pattern in self.MONTO_PATTERNS:
            match = re.search(pattern, text)
            if match:
                return f"${match.group(1)}"
        return None

    # ------------------------------------------------------------------
    # FECHA Y HORA DE LA OPERACIÓN
    # ------------------------------------------------------------------
    # Cubre tanto "10 de julio de 2026 - 12:34hs" como el formato real
    # de Mercado Pago "Viernes, 10 de julio de 2026 a las 21:10 hs"
    FECHA_LARGA_PATTERN = (
        r"(\d{1,2})\s+de\s+(" + "|".join(MESES.keys()) + r")\s+de\s+(\d{4})"
        r"(?:[\s\-,]*(?:a\s+las)?[\s\-,]*)?(\d{1,2}:\d{2})?\s*h?s?"
    )
    FECHA_CORTA_PATTERN = r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})[\s\-,]*?(\d{1,2}:\d{2})?"
    HORA_PATTERN = r"(\d{1,2}:\d{2})\s?h?s?"

    def extract_fecha_hora(self, text: str):
        """
        Busca la fecha y hora de la operación. Intenta primero el formato
        largo en español, luego el formato numérico corto. Devuelve un
        string "DD/MM/YYYY HH:MM" cuando puede normalizar, o None si no
        encuentra nada.
        """
        text_lower = text.lower()

        match = re.search(self.FECHA_LARGA_PATTERN, text_lower)
        if match:
            dia, mes_texto, anio, hora = match.groups()
            mes = MESES.get(mes_texto, "01")
            fecha_str = f"{dia.zfill(2)}/{mes}/{anio}"
            if hora:
                return f"{fecha_str} {hora}"
            hora_match = re.search(self.HORA_PATTERN, text_lower)
            if hora_match:
                return f"{fecha_str} {hora_match.group(1)}"
            return fecha_str

        match = re.search(self.FECHA_CORTA_PATTERN, text_lower)
        if match:
            fecha_str, hora = match.groups()
            fecha_str = fecha_str.replace("-", "/")
            if hora:
                return f"{fecha_str} {hora}"
            hora_match = re.search(self.HORA_PATTERN, text_lower)
            if hora_match:
                return f"{fecha_str} {hora_match.group(1)}"
            return fecha_str

        return None

    # ------------------------------------------------------------------
    # MOTIVO / CONCEPTO DE LA TRANSFERENCIA
    # ------------------------------------------------------------------
    MOTIVO_PATTERN = r"[Mm]otivo:?\s*([^\n]{1,60})"

    def extract_motivo(self, text: str):
        """Busca el motivo/concepto declarado en la transferencia
        (ej: "Motivo: Varios"). Devuelve None si no está presente."""
        match = re.search(self.MOTIVO_PATTERN, text)
        if match:
            motivo = match.group(1).strip()
            return motivo if motivo else None
        return None

    # ------------------------------------------------------------------
    # NÚMERO DE OPERACIÓN / COMPROBANTE
    # ------------------------------------------------------------------
    NUMERO_OP_PATTERNS = [
        r"[Nn]úmero\s+de\s+operaci[oó]n(?:\s+de\s+Mercado\s+Pago)?:?\s*\n?\s*(\d{5,})",
        r"[Nn]ro\.?\s*de\s+operaci[oó]n:?\s*\n?\s*(\d{5,})",
        r"[Oo]peraci[oó]n\s*[Nn]?[°º]?:?\s*\n?\s*(\d{5,})",
        r"[Cc]omprobante\s*[Nn]?[°º]?:?\s*\n?\s*(\d{5,})",
        r"[Ii][Dd]\s*(?:de)?\s*(?:transacci[oó]n|pago):?\s*\n?\s*(\d{5,})",
    ]

    def extract_numero_operacion(self, text: str):
        """Busca el número de operación/comprobante (identificador único
        de la transacción, generalmente de 10+ dígitos)."""
        for pattern in self.NUMERO_OP_PATTERNS:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    # ------------------------------------------------------------------
    # EMISOR / RECEPTOR (bloques "De" y "Para")
    # ------------------------------------------------------------------
    def _split_de_para_blocks(self, text: str):
        """
        Divide el texto en el bloque correspondiente a "De" (emisor) y
        el bloque correspondiente a "Para" (receptor), usando esas
        palabras como delimitadores. Devuelve una tupla (bloque_de,
        bloque_para); cualquiera de los dos puede ser "" si no se
        encontró la estructura esperada.
        """
        de_match = re.search(r"\bDe\b", text)
        para_match = re.search(r"\bPara\b", text)

        if not de_match or not para_match or para_match.start() <= de_match.end():
            return "", ""

        de_block = text[de_match.end():para_match.start()]

        numop_match = re.search(r"[Nn]úmero\s+de\s+operaci[oó]n", text)
        para_end = numop_match.start() if numop_match else len(text)
        para_block = text[para_match.end():para_end]

        return de_block, para_block

    def _extract_persona_data(self, block: str):
        """
        Dentro de un bloque de texto (el de "De" o el de "Para"), extrae
        el nombre de la persona/empresa, su CUIT/CUIL y su CVU.
        Devuelve una tupla (nombre, cuit_cuil, cvu), con None en los
        campos que no se pudieron detectar.
        """
        nombre = None
        for linea in block.split("\n"):
            linea = linea.strip(" \t*•·-")
            if not linea:
                continue
            primera_palabra = linea.lower().split()[0] if linea.split() else ""
            if any(primera_palabra.startswith(et) for et in ETIQUETAS_NO_NOMBRE):
                continue
            nombre = linea
            break

        cuit_match = re.search(r"CUIT/?CUIL:?\s*([\d\-]{8,15})", block, re.IGNORECASE)
        cuit_cuil = cuit_match.group(1) if cuit_match else None

        cvu_match = re.search(r"CVU:?\s*(\d{10,})", block, re.IGNORECASE)
        cvu = cvu_match.group(1) if cvu_match else None

        return nombre, cuit_cuil, cvu

    def extract_emisor_receptor(self, text: str) -> dict:
        """
        Extrae todos los datos de emisor y receptor a partir de los
        bloques "De" / "Para" del comprobante. Devuelve un diccionario
        con las claves: emisor, emisor_cuit_cuil, emisor_cvu, receptor,
        receptor_cuit_cuil, receptor_cvu. Los valores no encontrados
        quedan en None.
        """
        de_block, para_block = self._split_de_para_blocks(text)

        emisor_nombre, emisor_cuit, emisor_cvu = self._extract_persona_data(de_block)
        receptor_nombre, receptor_cuit, receptor_cvu = self._extract_persona_data(para_block)

        return {
            "emisor": emisor_nombre,
            "emisor_cuit_cuil": emisor_cuit,
            "emisor_cvu": emisor_cvu,
            "receptor": receptor_nombre,
            "receptor_cuit_cuil": receptor_cuit,
            "receptor_cvu": receptor_cvu,
        }

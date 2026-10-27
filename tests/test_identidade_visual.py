from openpyxl import Workbook
from reportlab.lib.units import mm

from identidade_visual import (
    aplicar_marca_cabecalho_excel,
    desenhar_marca_cabecalho_pdf,
)


def test_marca_f3_configura_cabecalho_de_impressao_excel():
    planilha = Workbook().active

    aplicar_marca_cabecalho_excel(planilha)

    cabecalho = planilha.oddHeader.right
    assert cabecalho.text == 'F3 Tecnologia'
    assert cabecalho.font == 'Arial,Bold'
    assert cabecalho.size == 10
    assert cabecalho.color == 'A9B9D0'


def test_marca_f3_e_desenhada_no_cabecalho_de_cada_pagina_pdf():
    class CanvasTeste:
        def __init__(self):
            self.textos = []

        def saveState(self):
            pass

        def setFillColorRGB(self, *cor):
            pass

        def setFillAlpha(self, alpha):
            self.alpha = alpha

        def setFont(self, *fonte):
            pass

        def drawRightString(self, x, y, texto):
            self.textos.append((x, y, texto))

        def restoreState(self):
            pass

    class DocumentoTeste:
        pagesize = (612, 792)

    canvas = CanvasTeste()

    desenhar_marca_cabecalho_pdf(canvas, DocumentoTeste())

    assert [texto for _, _, texto in canvas.textos] == ['F3', 'TECNOLOGIA']
    assert canvas.alpha == 0.24
    assert all(abs(x - (612 - 10 * mm)) < 0.01 for x, _, _ in canvas.textos)

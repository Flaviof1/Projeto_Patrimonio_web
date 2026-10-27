from reportlab.lib.units import mm


def aplicar_marca_cabecalho_excel(planilha):
    cabecalho = planilha.oddHeader.right
    cabecalho.text = 'F3 Tecnologia'
    cabecalho.font = 'Arial,Bold'
    cabecalho.size = 10
    cabecalho.color = 'A9B9D0'


def desenhar_marca_cabecalho_pdf(canvas, documento):
    canvas.saveState()
    canvas.setFillColorRGB(0.08, 0.25, 0.51)
    canvas.setFillAlpha(0.24)
    canvas.setFont('Helvetica-Bold', 11)
    x = documento.pagesize[0] - 10 * mm
    canvas.drawRightString(x, documento.pagesize[1] - 8 * mm, 'F3')
    canvas.setFont('Helvetica-Bold', 6.5)
    canvas.drawRightString(x, documento.pagesize[1] - 11 * mm, 'TECNOLOGIA')
    canvas.restoreState()

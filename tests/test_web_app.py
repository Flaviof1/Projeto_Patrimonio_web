import json
import os
import shutil
import tempfile
from pathlib import Path
from io import BytesIO

import pytest


_TEST_DATA = tempfile.TemporaryDirectory(prefix='patrimonio_web_test_')
os.environ['PATRIMONIO_WEB_DATA_DIR'] = _TEST_DATA.name

import pandas as pd
import zipfile
from PIL import Image
from openpyxl import load_workbook

from app import app
import rotas_leilao as leilao_routes
import rotas_patrimonio as patrimonio_routes
import servicos_comuns
import servicos_leilao
import servicos_operacoes
import servicos_patrimonio
from paths import (
    BACKUPS_DIR,
    DATA_DIR,
    DADOS_ENTRADA_DIR,
    DADOS_TRABALHO_DIR,
    FOTOS_DIR,
)
from preparar_dados import normalizar_plaqueta_numerica

CSV_VALIDO = (
    'Código;Plaqueta;Descrição;Modelo;Setor Atual;Data\n'
    '001;000123;Mesa;M1;Setor A;01/02/2024\n'
    '002;000124;Cadeira;C2;Setor B;15/03/2023\n'
)


@pytest.fixture(autouse=True)
def limpar_dados():
    pasta_real = Path(__file__).resolve().parents[1] / 'dados_web'
    assert DATA_DIR.resolve() != pasta_real.resolve(), (
        'Os testes devem usar uma pasta temporária, nunca dados_web do projeto.'
    )
    for pasta in ('dados_entrada', 'dados_trabalho', 'relatorios', 'backups'):
        caminho = DATA_DIR / pasta
        if caminho.exists():
            shutil.rmtree(caminho)
    yield


@pytest.fixture
def client():
    with app.test_client() as cliente:
        cliente.get('/')
        with cliente.session_transaction() as sessao:
            sessao['csrf_token'] = 'token-de-teste'
        yield cliente


def post(client, url, **kwargs):
    headers = kwargs.pop('headers', {})
    headers['X-CSRF-Token'] = 'token-de-teste'
    return client.post(url, headers=headers, **kwargs)


def importar_arquivo_leilao_aprovado(client, conteudo, nome, lote_id=None):
    dados = {
        'arquivo': (BytesIO(conteudo), nome),
        'lote_id': lote_id or '',
    }
    analise = post(
        client,
        '/api/leilao/importar/analisar',
        data=dados,
        content_type='multipart/form-data',
    )
    if analise.status_code != 200:
        return analise
    resultado = analise.get_json()
    confirmacao = post(
        client,
        '/api/leilao/importar',
        data={
            'arquivo': (BytesIO(conteudo), nome),
            'lote_id': lote_id or '',
            'token_analise': resultado['token_analise'],
            'indices_aprovados': json.dumps([
                proposta['indice'] for proposta in resultado['analise']['propostas']
            ]),
        },
        content_type='multipart/form-data',
    )
    return confirmacao


def enviar_csv(client, conteudo=CSV_VALIDO, confirmar=None):
    data = {'arquivo': (BytesIO(conteudo.encode('latin1')), 'cadastro.csv')}
    if confirmar:
        data['confirmar_substituicao'] = 'sim'
    return post(client, '/api/importar/csv', data=data, content_type='multipart/form-data')


def enviar_auxiliar(client, tipo, nome, conteudo):
    return post(
        client,
        '/api/importar/auxiliar',
        data={
            'tipo': tipo,
            'arquivo': (BytesIO(conteudo), nome),
        },
        content_type='multipart/form-data',
    )


def enviar_resultado(client, workbook, confirmar=None):
    data = {'arquivo': (BytesIO(workbook), 'conferencia_resultado.xlsx')}
    if confirmar:
        data['confirmado'] = 'sim'
    return post(
        client,
        '/api/importar/resultado',
        data=data,
        content_type='multipart/form-data',
    )


def criar_resultado_excel(linhas):
    arquivo = BytesIO()
    pd.DataFrame(linhas).to_excel(arquivo, index=False)
    return arquivo.getvalue()


def preparar_base(client):
    resposta = enviar_csv(client)
    assert resposta.status_code == 200, resposta.get_json()
    return post(client, '/api/preparar', data={})


def test_aplicacao_inicia_sem_abrir_dados_do_desktop(client):
    resposta = client.get('/api/status')

    assert resposta.status_code == 200
    assert resposta.get_json()['estado'] == 'sem_dados'
    pagina = client.get('/')
    assert b'alt="F3 Tecnologia"' in pagina.data
    assert b'id="csv-import-followup"' in pagina.data
    assert b'id="prepare-after-csv"' in pagina.data
    logo = client.get('/static/f3-tecnologia.svg')
    assert logo.status_code == 200
    assert b'<title id="title">F3 Tecnologia</title>' in logo.data
    assert not (
        DATA_DIR / 'dados_trabalho'
    ).joinpath('conferencia_resultado.xlsx').exists()


def test_modo_mysql_usa_a_mesma_camada_de_leitura_e_gravacao(client, monkeypatch):
    base = pd.DataFrame([{
        'Código': '001',
        'Plaqueta': '000123',
        'Descrição': 'Mesa',
        'Setor Atual': 'Setor A',
        'STATUS': 'PEND',
        'ESTADO': 'EM USO',
        'OBSERVACAO': '',
        'Foto': '',
    }])
    gravado = {}

    class ArmazenamentoTeste:
        @classmethod
        def from_environment(cls):
            return cls()

        def load(self):
            return base.copy()

        def replace_all(self, dados):
            gravado['dados'] = dados.copy()

    monkeypatch.setattr(servicos_patrimonio, 'MySQLStore', ArmazenamentoTeste)
    monkeypatch.setattr(servicos_operacoes, 'MySQLStore', ArmazenamentoTeste)
    monkeypatch.setenv('PATRIMONIO_STORAGE', 'mysql')

    status = client.get('/api/status').get_json()
    assert status['arquivo'] == 'MySQL'
    assert status['total'] == 1

    item = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens'][0]
    resposta = post(
        client,
        f"/api/itens/{item['id']}/editar",
        json={'status': 'OK', 'estado': 'DESUSO', 'observacao': 'Gravado no MySQL'},
    )

    assert resposta.status_code == 200
    assert len(gravado['dados']) == 1
    assert gravado['dados'].loc[item['id'], 'OBSERVACAO'] == 'Gravado no MySQL'


def test_normalizacao_plaqueta_nao_acrescenta_digito_apos_leitura_excel():
    assert normalizar_plaqueta_numerica(123.0) == '000123'
    assert normalizar_plaqueta_numerica('000123') == '000123'


def test_importacao_csv_valida_e_preserva_arquivo_ao_receber_csv_invalido(client):
    resposta = enviar_csv(client)
    assert resposta.status_code == 200
    assert resposta.get_json()['registros'] == 2
    arquivo = DADOS_ENTRADA_DIR / 'Localizar_Bens.csv'
    original = arquivo.read_bytes()

    invalido = enviar_csv(client, 'Sem as colunas obrigatórias\n1;2;3\n', confirmar=True)

    assert invalido.status_code == 400
    assert arquivo.read_bytes() == original


def test_importa_baixados_e_preparacao_marca_status_por_plaqueta(client):
    assert enviar_csv(client).status_code == 200
    planilha = BytesIO()
    pd.DataFrame({'Plaqueta': [123, 999]}).to_excel(planilha, index=False)
    importacao = enviar_auxiliar(
        client, 'baixados', 'Bens_Baixados.xlsx', planilha.getvalue()
    )
    assert importacao.status_code == 200, importacao.get_json()

    preparar = post(client, '/api/preparar', data={})

    assert preparar.status_code == 200, preparar.get_json()
    resumo = preparar.get_json()['baixados']
    assert resumo == {
        'na_planilha': 2,
        'encontrados_no_cadastro': 1,
        'marcados_baixa': 1,
        'prevaleceram_alienados': 0,
        'nao_encontrados': 1,
    }
    itens = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens']
    baixado = next(item for item in itens if item['Plaqueta'] == '000123')
    assert baixado['STATUS'] == 'BAIXA'
    assert baixado['ESTADO'] == 'NAO_APLICAVEL'


def test_importacao_baixados_rejeita_planilha_sem_coluna_plaqueta(client):
    planilha = BytesIO()
    pd.DataFrame({'Codigo': ['001']}).to_excel(planilha, index=False)

    resposta = enviar_auxiliar(
        client, 'baixados', 'Bens_Baixados.xlsx', planilha.getvalue()
    )

    assert resposta.status_code == 400
    assert 'Plaqueta' in resposta.get_json()['error']


def test_interface_mantem_formularios_de_fontes_sem_ods_ou_importacao_zip(client):
    html = client.get('/').get_data(as_text=True)

    assert 'alienados-form' in html
    assert 'baixados-form' in html
    assert 'id="sector-form"' in html
    assert 'type="submit">Iniciar</button>' in html
    assert 'id="clear-search"' in html
    assert 'Limpar filtros' in html
    assert 'Comparar bens baixados' not in html
    assert 'Migração do desktop' not in html
    assert 'result-form' not in html
    assert 'photos-form' not in html
    assert 'sem-plaqueta-form' not in html
    assert post(
        client,
        '/api/importar/fotos',
        data={},
        content_type='multipart/form-data',
    ).status_code == 404


def test_importacao_resultado_migra_edicoes_e_preserva_referencias_de_fotos(client):
    planilha = criar_resultado_excel([{
        'Código': '001',
        'Plaqueta': '000123',
        'Descrição': 'Mesa conferida',
        'Setor Atual': 'Setor A',
        'STATUS': 'OK',
        'ESTADO': 'EM USO',
        'OBSERVACAO': 'Conferência migrada',
        'Foto': 'foto-desktop.jpg',
        'ALTERADO_EM': '20/10/2026 10:00:00',
    }])

    resposta = enviar_resultado(client, planilha)

    assert resposta.status_code == 200, resposta.get_json()
    assert resposta.get_json()['bens'] == 1
    assert resposta.get_json()['setores'] == 1
    assert resposta.get_json()['referencias_foto_preservadas'] == 1
    item = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens'][0]
    assert item['STATUS'] == 'OK'
    assert item['ESTADO'] == 'EM USO'
    assert item['OBSERVACAO'] == 'Conferência migrada'
    assert item['ALTERADO_EM'] == '20/10/2026 10:00:00'
    assert item['Foto'] == 'foto-desktop.jpg'
    assert not (DADOS_ENTRADA_DIR / 'conferencia_resultado.xlsx').exists()


def test_migracao_resultado_exige_confirmacao_faz_backup_e_preserva_base_se_cancelada(client):
    assert preparar_base(client).status_code == 200
    planilha = criar_resultado_excel([{
        'Código': '901',
        'Plaqueta': '009999',
        'Descrição': 'Bem migrado',
        'Setor Atual': 'Setor Z',
        'STATUS': 'DIVERG',
        'ESTADO': 'DESUSO',
        'OBSERVACAO': 'Migrado',
    }])

    precisa_confirmar = enviar_resultado(client, planilha)
    assert precisa_confirmar.status_code == 409
    assert precisa_confirmar.get_json()['requires_confirmation'] is True
    assert precisa_confirmar.get_json()['preview']['bens'] == 1
    assert client.get('/api/conferencia?setor=Setor%20A').get_json()['total'] == 1

    migrado = enviar_resultado(client, planilha, confirmar=True)
    assert migrado.status_code == 200, migrado.get_json()
    assert client.get('/api/conferencia?setor=Setor%20Z').get_json()['total'] == 1
    assert list(BACKUPS_DIR.glob('backup_web_*.zip'))


@pytest.mark.parametrize(
    'linhas',
    [
        [{'Descrição': 'Sem colunas obrigatórias'}],
        [{
            'Plaqueta': '000123',
            'Descrição': 'Status desconhecido',
            'Setor Atual': 'Setor A',
            'STATUS': 'DESCONHECIDO',
        }],
    ],
)
def test_planilha_resultado_invalida_nao_substitui_base(linhas, client):
    assert preparar_base(client).status_code == 200
    resposta = enviar_resultado(client, criar_resultado_excel(linhas))

    assert resposta.status_code == 400
    assert client.get('/api/conferencia?setor=Setor%20A').get_json()['total'] == 1


def test_preparacao_setores_conferencia_e_regras_de_estado(client):
    preparar = preparar_base(client)
    assert preparar.status_code == 200, preparar.get_json()
    assert preparar.get_json()['total'] == 2

    setores = client.get('/api/setores').get_json()['setores']
    assert setores == ['Setor A', 'Setor B']

    conferencia = client.get('/api/conferencia?setor=Setor%20A').get_json()
    assert conferencia['total'] == 1
    item = conferencia['itens'][0]
    assert item['STATUS'] == 'PEND'

    edicao = post(
        client,
        f"/api/itens/{item['id']}/editar",
        json={
            'status': 'N_LOC',
            'estado': 'EM USO',
            'observacao': 'Não localizado',
            'valor': '1250,50',
        },
    )
    assert edicao.status_code == 200, edicao.get_json()
    atualizado = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens'][0]
    assert atualizado['STATUS'] == 'N_LOC'
    assert atualizado['ESTADO'] == 'NAO_APLICAVEL'
    assert atualizado['OBSERVACAO'] == 'Não localizado'
    assert float(atualizado['Valor']) == pytest.approx(1250.50)


def test_tabelas_gerais_exibem_valor_atual_e_valor_informado(client):
    csv_com_valores = (
        'Código;Plaqueta;Descrição;Modelo;Setor Atual;Data;Valor Atual\n'
        '001;000123;Mesa;M1;Setor A;01/02/2024;1.234,56\n'
        '002;000124;Cadeira;C2;Setor B;15/03/2023;250,00\n'
    )
    assert enviar_csv(client, csv_com_valores).status_code == 200
    assert post(client, '/api/preparar', data={}).status_code == 200

    item = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens'][0]
    assert item['Valor Atual'] == '1.234,56'
    atualizado = post(
        client,
        f"/api/itens/{item['id']}/editar",
        json={'status': 'PEND', 'estado': 'EM USO', 'valor': '75,25'},
    )
    assert atualizado.status_code == 200

    busca = client.get('/api/busca?termo=Mesa').get_json()['itens'][0]
    assert busca['Valor Atual'] == '1.234,56'
    assert busca['Valor'] == pytest.approx(75.25)


def test_edicao_patrimonial_rejeita_valor_invalido_sem_salvar(client):
    assert preparar_base(client).status_code == 200
    item = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens'][0]

    resposta = post(
        client,
        f"/api/itens/{item['id']}/editar",
        json={'status': 'OK', 'estado': 'EM USO', 'valor': '-1'},
    )

    assert resposta.status_code == 400
    assert 'valor' in resposta.get_json()['error'].lower()


def test_edicao_inclusao_busca_e_repreparacao_preservam_conferencia(client):
    assert preparar_base(client).status_code == 200
    item = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens'][0]

    incluir = post(
        client,
        '/api/conferencia/incluir',
        json={
            'setor': 'Setor A',
            'descricao': 'Bem localizado sem plaqueta',
            'quantidade': 2,
            'status': 'DIVERG',
            'estado': 'EM USO',
        },
    )
    assert incluir.status_code == 200, incluir.get_json()

    busca = client.get('/api/busca?termo=plaqueta&setor=Setor+A&status=DIVERG')
    assert busca.status_code == 200
    assert busca.get_json()['total'] == 2

    editar = post(
        client,
        f"/api/itens/{item['id']}/editar",
        json={'status': 'OK', 'estado': 'EM USO', 'observacao': 'Conferido'},
    )
    assert editar.status_code == 200
    segunda_preparacao = post(client, '/api/preparar', data={'confirmado': 'sim'})
    assert segunda_preparacao.status_code == 200, segunda_preparacao.get_json()

    conferidos = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens']
    item_original = next(
        (linha for linha in conferidos if str(linha['Plaqueta']) == '000123'),
        None,
    )
    assert item_original is not None, [
        (linha.get('Plaqueta'), linha.get('Setor Atual'), linha.get('STATUS'))
        for linha in conferidos
    ]
    assert item_original['STATUS'] == 'OK'
    assert item_original['OBSERVACAO'] == 'Conferido'


def test_importa_fontes_auxiliares_e_preserva_regras_de_preparacao(client):
    assert enviar_csv(client).status_code == 200

    xlsx = BytesIO()
    pd.DataFrame([{
        'Plaqueta': '000123',
        'Bem / Descrição': 'Mesa alienada',
        'Data': '10/01/2024',
    }]).to_excel(xlsx, index=False)
    resposta_xlsx = enviar_auxiliar(
        client, 'alienados', 'Bens_Alienados.xlsx', xlsx.getvalue()
    )
    assert resposta_xlsx.status_code == 200, resposta_xlsx.get_json()

    baixados_xlsx = BytesIO()
    pd.DataFrame({'Plaqueta': ['000123', '000124']}).to_excel(
        baixados_xlsx, index=False
    )
    resposta_baixados = enviar_auxiliar(
        client, 'baixados', 'Bens_Baixados.xlsx', baixados_xlsx.getvalue()
    )
    assert resposta_baixados.status_code == 200, resposta_baixados.get_json()

    ods = BytesIO()
    xml = '''<?xml version="1.0" encoding="UTF-8"?>
    <office:document-content
      xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
      xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
      xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
      <office:body><office:spreadsheet><table:table>
        <table:table-row>
          <table:table-cell><text:p>Plaqueta</text:p></table:table-cell>
          <table:table-cell><text:p>Descrição</text:p></table:table-cell>
          <table:table-cell><text:p>Setor Atual</text:p></table:table-cell>
          <table:table-cell><text:p>Estado</text:p></table:table-cell>
          <table:table-cell><text:p>Observação</text:p></table:table-cell>
        </table:table-row>
        <table:table-row>
          <table:table-cell><text:p></text:p></table:table-cell>
          <table:table-cell><text:p>Bem achado</text:p></table:table-cell>
          <table:table-cell><text:p>Setor B</text:p></table:table-cell>
          <table:table-cell><text:p>DESUSO</text:p></table:table-cell>
          <table:table-cell><text:p>Sem plaqueta oficial</text:p></table:table-cell>
        </table:table-row>
      </table:table></office:spreadsheet></office:body>
    </office:document-content>'''.encode('utf-8')
    with zipfile.ZipFile(ods, 'w', zipfile.ZIP_DEFLATED) as arquivo_zip:
        arquivo_zip.writestr('content.xml', xml)
    resposta_ods = enviar_auxiliar(
        client, 'sem_plaqueta', 'itens_inclusos_sem_plaquetas.ods', ods.getvalue()
    )
    assert resposta_ods.status_code == 200, resposta_ods.get_json()

    preparar = post(client, '/api/preparar', data={})
    assert preparar.status_code == 200, preparar.get_json()
    assert preparar.get_json()['avisos'] == [
        '1 plaqueta(s) da lista de baixados também constam como alienadas; o status ALIEN foi mantido.'
    ]
    assert preparar.get_json()['baixados'] == {
        'na_planilha': 2,
        'encontrados_no_cadastro': 2,
        'marcados_baixa': 1,
        'prevaleceram_alienados': 1,
        'nao_encontrados': 0,
    }

    alienados = client.get('/api/conferencia?setor=Alienados').get_json()['itens']
    assert len(alienados) == 1
    assert alienados[0]['STATUS'] == 'ALIEN'
    achados = client.get('/api/conferencia?setor=Setor%20B').get_json()['itens']
    item_baixado = next(item for item in achados if item['Plaqueta'] == '000124')
    assert item_baixado['STATUS'] == 'BAIXA'
    assert any(item['Descrição'] == 'Bem achado' for item in achados)


def test_anexa_remove_e_filtra_foto_de_bem(client):
    assert preparar_base(client).status_code == 200
    assert client.get('/api/busca?foto=Sem+foto').get_json()['total'] == 2
    assert client.get('/api/busca?foto=Com+foto').get_json()['total'] == 0


    foto = BytesIO()
    Image.new('RGB', (1, 1), color=(20, 40, 60)).save(foto, format='PNG')
    resposta = post(
        client,
        '/api/itens/0/foto',
        data={
            'arquivo': (BytesIO(foto.getvalue()), 'foto.png'),
            'status': 'OK',
            'estado': 'DESUSO',
            'observacao': 'Foto e conferência salvas juntas',
            'valor': '75,25',
        },
        content_type='multipart/form-data',
    )
    assert resposta.status_code == 200, resposta.get_json()
    item = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens'][0]
    assert item['Foto']
    assert item['STATUS'] == 'OK'
    assert item['ESTADO'] == 'DESUSO'
    assert item['OBSERVACAO'] == 'Foto e conferência salvas juntas'
    assert float(item['Valor']) == pytest.approx(75.25)
    assert resposta.get_json()['pasta'] == 'dados_trabalho\\Fotos'
    assert (FOTOS_DIR / item['Foto']).is_file()
    for nome_planilha in ('conferencia_resultado.xlsx', 'conferencia_campo.xlsx'):
        dados_salvos = pd.read_excel(DADOS_TRABALHO_DIR / nome_planilha).fillna('')
        assert dados_salvos.loc[0, 'Foto'] == item['Foto']
        assert dados_salvos.loc[0, 'ESTADO'] == 'DESUSO'
    assert client.get(f"/dados-web/fotos/{item['Foto']}").status_code == 200
    com_foto = client.get('/api/busca?foto=Com+foto')
    assert com_foto.get_json()['total'] == 1

    removida = post(
        client,
        '/api/itens/0/foto',
        data={'remover': 'sim'},
        content_type='multipart/form-data',
    )
    assert removida.status_code == 200, removida.get_json()
    sem_foto = client.get('/api/busca?foto=Sem+foto')
    assert sem_foto.get_json()['total'] == 2


def test_busca_filtra_itens_com_e_sem_plaqueta(client, monkeypatch):
    pagina = client.get('/').get_data(as_text=True)
    assert 'id="search-plaqueta"' in pagina
    assert '<option value="Sem plaqueta">Sem plaqueta</option>' in pagina

    dados = pd.DataFrame([
        {'Plaqueta': '', 'Descrição': 'Bem sem plaqueta', 'STATUS': 'DIVERG'},
        {'Plaqueta': None, 'Descrição': 'Outro sem plaqueta', 'STATUS': 'DIVERG'},
        {'Plaqueta': '000123', 'Descrição': 'Bem identificado', 'STATUS': 'OK'},
    ])
    monkeypatch.setattr(patrimonio_routes, '_carregar_base', lambda: dados)

    sem_plaqueta = client.get('/api/busca?plaqueta=Sem+plaqueta').get_json()
    com_plaqueta = client.get('/api/busca?plaqueta=Com+plaqueta').get_json()

    assert sem_plaqueta['total'] == 2
    assert all(not item['Plaqueta'] for item in sem_plaqueta['itens'])
    assert com_plaqueta['total'] == 1
    assert com_plaqueta['itens'][0]['Plaqueta'] == '000123'


def test_busca_exclui_em_lote_itens_selecionados(client, monkeypatch):
    base = pd.DataFrame([
        {'Plaqueta': '000123', 'Descrição': 'Mesa', 'Setor Atual': 'Setor A'},
        {'Plaqueta': '', 'Descrição': 'Bem sem plaqueta', 'Setor Atual': 'Setor A'},
        {'Plaqueta': '000124', 'Descrição': 'Cadeira', 'Setor Atual': 'Setor B'},
    ])
    gravado = {}
    monkeypatch.setattr(patrimonio_routes, '_carregar_base', lambda: base.copy())
    monkeypatch.setattr(patrimonio_routes, '_salvar_planilha', lambda dados: gravado.update(base=dados.copy()))

    resposta = post(client, '/api/itens/excluir-lote', json={'ids': [0, 2]})

    assert resposta.status_code == 200, resposta.get_json()
    assert resposta.get_json()['mensagem'].startswith('2 item(ns) excluído(s)')
    assert gravado['base']['Descrição'].tolist() == ['Bem sem plaqueta']


def test_exclusao_em_lote_rejeita_selecao_invalida_sem_salvar(client, monkeypatch):
    base = pd.DataFrame([{'Plaqueta': '000123', 'Descrição': 'Mesa'}])
    salvou = []
    monkeypatch.setattr(patrimonio_routes, '_carregar_base', lambda: base.copy())
    monkeypatch.setattr(patrimonio_routes, '_salvar_planilha', lambda dados: salvou.append(dados))

    resposta = post(client, '/api/itens/excluir-lote', json={'ids': [0, 99]})

    assert resposta.status_code == 404
    assert salvou == []


def test_busca_e_conferencia_paginam_resultados(client, monkeypatch):
    dados = pd.DataFrame([
        {
            'Plaqueta': str(indice),
            'Descrição': f'Bem {indice}',
            'Setor Atual': 'Setor A',
            'STATUS': 'PEND',
        }
        for indice in range(120)
    ])
    monkeypatch.setattr(patrimonio_routes, '_carregar_base', lambda: dados)

    primeira_pagina = client.get('/api/busca').get_json()
    segunda_pagina = client.get('/api/busca?pagina=2').get_json()
    ultima_pagina = client.get('/api/busca?pagina=99').get_json()
    conferencia = client.get('/api/conferencia?setor=Setor+A&pagina=3').get_json()

    assert primeira_pagina['total'] == 120
    assert len(primeira_pagina['itens']) == 50
    assert primeira_pagina['pagina'] == 1
    assert len(segunda_pagina['itens']) == 50
    assert segunda_pagina['itens'][0]['Plaqueta'] == '50'
    assert ultima_pagina['pagina'] == 3
    assert len(ultima_pagina['itens']) == 20
    assert conferencia['total'] == 120
    assert len(conferencia['itens']) == 20

    invalida = client.get('/api/busca?por_pagina=101')
    assert invalida.status_code == 400


def test_lista_leilao_importa_csv_e_permite_vinculo_confirmado(client, monkeypatch):
    base = pd.DataFrame([
        {
            'Plaqueta': plaqueta,
            'Descrição': 'Mesa de reunião',
            'Setor Atual': 'Setor A',
            'STATUS': status,
            'ESTADO': 'EM USO',
        }
        for plaqueta, status in (
            ('000123', 'PEND'),
            ('000124', 'OK'),
            ('000125', 'DIVERG'),
            ('000126', 'DIV'),
            ('', 'PEND'),
            ('', 'N_LOC'),
        )
    ], index=[7, 8, 9, 10, 12, 13])
    monkeypatch.setattr(leilao_routes, '_carregar_base', lambda: base)

    importacao = importar_arquivo_leilao_aprovado(
        client,
        'Descrição;Setor;Data\nMesa reunião;Setor A;2020-01-01\n'.encode(),
        'leilao.csv',
    )
    assert importacao.status_code == 200, importacao.get_json()
    assert importacao.get_json()['importados'] == 1

    item = client.get('/api/leilao').get_json()['itens'][0]
    assert item['situacao'] == 'PENDENTE_IDENTIFICACAO'
    assert 'sugestoes' not in item
    sugestoes = client.get(f"/api/leilao/{item['id']}/sugestoes").get_json()
    assert [
        (sugestao['id'], sugestao['plaqueta'], sugestao['status'])
        for sugestao in sugestoes['sugestoes']
    ] == [
        (7, '000123', 'PEND'),
        (9, '000125', 'DIVERG'),
        (10, '000126', 'DIV'),
        (12, '', 'PEND'),
        (13, '', 'N_LOC'),
    ]
    assert base.iloc[0]['STATUS'] == 'PEND'

    vinculo = post(
        client,
        f"/api/leilao/{item['id']}/vincular",
        json={'plaqueta': '000123'},
    )
    assert vinculo.status_code == 200, vinculo.get_json()
    assert 'Nenhuma baixa foi realizada' in vinculo.get_json()['mensagem']
    atualizado = client.get('/api/leilao').get_json()['itens'][0]
    assert atualizado['situacao'] == 'IDENTIFICADO'
    assert atualizado['plaqueta_vinculada'] == '000123'
    assert base.iloc[0]['STATUS'] == 'PEND'


def test_vinculo_sem_plaqueta_por_identificador_avisa_status_diverg(client, monkeypatch):
    base = pd.DataFrame([{
        'Plaqueta': '',
        'Descrição': 'Cadeira localizada sem plaqueta',
        'Setor Atual': 'Setor B',
        'STATUS': 'DIVERG',
        'ESTADO': 'EM USO',
    }], index=[12])
    monkeypatch.setattr(leilao_routes, '_carregar_base', lambda: base)

    incluido = post(
        client,
        '/api/leilao/manual',
        json={'descricao': 'Cadeira localizada sem plaqueta'},
    )
    item_id = incluido.get_json()['item']['id']
    sugestoes = client.get(f'/api/leilao/{item_id}/sugestoes').get_json()['sugestoes']

    assert len(sugestoes) == 1
    assert sugestoes[0]['id'] == 12
    assert sugestoes[0]['plaqueta'] == ''
    assert sugestoes[0]['status'] == 'DIVERG'

    vinculo = post(
        client,
        f'/api/leilao/{item_id}/vincular',
        json={'identificador': 12},
    )

    assert vinculo.status_code == 200, vinculo.get_json()
    assert 'Atenção' in vinculo.get_json()['mensagem']
    assert 'setor' in vinculo.get_json()['mensagem']
    item_vinculado = client.get('/api/leilao').get_json()['itens'][0]
    assert item_vinculado['situacao'] == 'IDENTIFICADO'
    assert item_vinculado['identificador_vinculado'] == 12
    assert item_vinculado['descricao_vinculada'] == 'Cadeira localizada sem plaqueta'
    assert item_vinculado['setor_vinculado'] == 'Setor B'
    assert base.iloc[0]['STATUS'] == 'DIVERG'


def test_importacao_analisa_setores_por_plaqueta_e_somente_aplica_apos_aprovacao(
    client, monkeypatch
):
    base = pd.DataFrame([
        {'Plaqueta': '000123', 'Descrição': 'Mesa cadastrada', 'Setor Atual': 'Setor A'},
        {'Plaqueta': '000124', 'Descrição': 'Cadeira 1', 'Setor Atual': 'Setor B'},
        {'Plaqueta': '000124', 'Descrição': 'Cadeira 2', 'Setor Atual': 'Setor C'},
    ])
    monkeypatch.setattr(leilao_routes, '_carregar_base', lambda: base)
    conteudo = (
        'Descrição;Plaqueta;Setor;Valor\n'
        'Mesa;123;Setor informado;1.234,56\n'
        'Armário sem plaqueta;;Setor manual;50,00\n'
        'Bem não encontrado;999;Setor antigo;60,00\n'
        'Cadeira ambígua;000124;Setor antigo;70,00\n'
    ).encode()

    analise = post(
        client,
        '/api/leilao/importar/analisar',
        data={'arquivo': (BytesIO(conteudo), 'leilao.csv')},
        content_type='multipart/form-data',
    )

    assert analise.status_code == 200, analise.get_json()
    resultado = analise.get_json()
    assert client.get('/api/leilao').get_json()['itens'] == []
    assert resultado['analise']['resumo'] == {
        'total': 4,
        'descricao_provisoria': 0,
        'propostas': 1,
        'sem_plaqueta': 1,
        'nao_encontrada': 1,
        'ambiguas': 1,
        'sem_setor': 0,
        'setor_igual': 0,
        'sem_base': 0,
        'descricoes_encontradas': 0,
    }
    assert resultado['analise']['propostas'][0]['setor_sugerido'] == 'Setor A'

    importacao = post(
        client,
        '/api/leilao/importar',
        data={
            'arquivo': (BytesIO(conteudo), 'leilao.csv'),
            'token_analise': resultado['token_analise'],
            'indices_aprovados': '[0]',
        },
        content_type='multipart/form-data',
    )

    assert importacao.status_code == 200, importacao.get_json()
    itens = client.get('/api/leilao').get_json()['itens']
    assert all(item['lote_id'] is None for item in itens)
    assert [item['setor'] for item in itens] == [
        'Setor A',
        'Setor manual',
        'Setor antigo',
        'Setor antigo',
    ]
    assert [item['valor'] for item in itens] == ['1234.56', '50.00', '60.00', '70.00']
    assert base['Setor Atual'].tolist() == ['Setor A', 'Setor B', 'Setor C']


def test_importacao_leilao_inclui_linhas_com_plaqueta_sem_descricao(client, monkeypatch):
    monkeypatch.setattr(leilao_routes, '_carregar_base', lambda: None)
    conteudo = (
        'Descrição;Plaqueta;LOTE\n'
        ';123;2\n'
        'Cadeira sem plaqueta;;2\n'
        ';;2\n'
    ).encode()

    analise = post(
        client,
        '/api/leilao/importar/analisar',
        data={'arquivo': (BytesIO(conteudo), 'leilao.csv')},
        content_type='multipart/form-data',
    )

    assert analise.status_code == 200, analise.get_json()
    resultado = analise.get_json()
    assert resultado['analise']['resumo']['total'] == 2
    assert resultado['analise']['resumo']['descricao_provisoria'] == 1
    assert resultado['analise']['resumo']['sem_plaqueta'] == 1

    importacao = post(
        client,
        '/api/leilao/importar',
        data={
            'arquivo': (BytesIO(conteudo), 'leilao.csv'),
            'token_analise': resultado['token_analise'],
            'indices_aprovados': '[]',
        },
        content_type='multipart/form-data',
    )

    assert importacao.status_code == 200, importacao.get_json()
    itens = client.get('/api/leilao').get_json()['itens']
    assert len(itens) == 2
    assert itens[0]['descricao'] == 'Item com plaqueta 123'
    assert itens[0]['plaqueta_informada'] == '123'
    assert 'descricao_provisoria' not in itens[0]
    assert itens[1]['descricao'] == 'Cadeira sem plaqueta'


def test_importacao_leilao_salva_descricao_do_cadastro_pela_plaqueta(
    client, monkeypatch
):
    base = pd.DataFrame([{
        'Plaqueta': '123',
        'Descrição': 'Mesa cadastrada',
        'Setor Atual': 'Setor A',
    }])
    monkeypatch.setattr(leilao_routes, '_carregar_base', lambda: base)
    conteudo = 'Descrição;Plaqueta;Setor\n;123;Setor A\n'.encode()

    importacao = importar_arquivo_leilao_aprovado(
        client, conteudo, 'leilao.csv'
    )

    assert importacao.status_code == 200, importacao.get_json()
    resposta = importacao.get_json()
    assert resposta['descricoes_aplicadas'] == 1
    item = client.get('/api/leilao').get_json()['itens'][0]
    assert item['descricao'] == 'Mesa cadastrada'
    assert item['plaqueta_informada'] == '123'
    assert item['setor'] == 'Setor A'
    assert 'descricao_provisoria' not in item


def test_exportar_leilao_excel_respeita_filtros_e_inclui_todos_os_itens(client):
    lote_a = post(
        client,
        '/api/leilao/lotes',
        json={'nome': 'Depósito A'},
    ).get_json()['lote']
    lote_b = post(
        client,
        '/api/leilao/lotes',
        json={'nome': 'Depósito B'},
    ).get_json()['lote']
    conteudo_a = (
        'Descrição;Plaqueta\n'
        + ''.join(f'Mesa {indice};{1000 + indice}\n' for indice in range(1, 12))
    ).encode()
    conteudo_b = 'Descrição;Plaqueta\nCadeira;2000\n'.encode()
    importacao_a = importar_arquivo_leilao_aprovado(
        client, conteudo_a, 'lote-a.csv', lote_a['id']
    )
    importacao_b = importar_arquivo_leilao_aprovado(
        client, conteudo_b, 'lote-b.csv', lote_b['id']
    )
    assert importacao_a.status_code == 200
    assert importacao_b.status_code == 200

    resposta = client.get(
        f"/api/leilao/exportar?q=mesa&lote_id={lote_a['id']}"
    )

    assert resposta.status_code == 200
    assert resposta.mimetype == (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    dados = pd.read_excel(BytesIO(resposta.data), dtype=object)
    assert len(dados) == 11
    assert dados['Descrição'].tolist() == [
        f'Mesa {indice}' for indice in range(1, 12)
    ]
    assert dados['Lote'].tolist() == [
        'Lote 1 · Depósito A',
    ] * 11
    assert 'Plaqueta vinculada' in dados.columns


def test_importacao_leilao_usa_coluna_lote_e_avisa_divergencia_antes_de_gravar(client):
    lote_um = post(
        client,
        '/api/leilao/lotes',
        json={'nome': 'Depósito A'},
    ).get_json()['lote']
    lote_dois = post(
        client,
        '/api/leilao/lotes',
        json={'nome': 'Depósito B'},
    ).get_json()['lote']
    planilha = BytesIO()
    pd.DataFrame([
        {'Descrição': 'Mesa 1', 'LOTE': 1},
        {'Descrição': 'Mesa 2', 'LOTE': 'Lote 01'},
        {'Descrição': 'Mesa 3', 'LOTE': 2},
        {'Descrição': 'Mesa 4', 'LOTE': ''},
        {'Descrição': 'Mesa 5', 'LOTE': 3},
    ]).to_excel(planilha, index=False)
    conteudo = planilha.getvalue()

    analise = post(
        client,
        '/api/leilao/importar/analisar',
        data={
            'arquivo': (BytesIO(conteudo), 'leilao.xlsx'),
            'lote_id': lote_dois['id'],
        },
        content_type='multipart/form-data',
    )

    assert analise.status_code == 200, analise.get_json()
    resultado = analise.get_json()
    plano = resultado['analise']['lotes_importacao']
    assert plano['coluna_detectada'] is True
    assert plano['lotes_criados'] == ['Lote 03']
    assert [
        (grupo['nome'], grupo['quantidade'])
        for grupo in plano['grupos']
    ] == [
        ('Depósito A', 2),
        ('Depósito B', 2),
        ('Lote 03', 1),
    ]
    assert [
        (divergencia['lote_planilha'], divergencia['quantidade'])
        for divergencia in plano['divergencias']
    ] == [('Depósito A', 2), ('Lote 03', 1)]
    estado_antes_da_confirmacao = client.get('/api/leilao').get_json()
    assert estado_antes_da_confirmacao['itens'] == []
    assert [lote['nome'] for lote in estado_antes_da_confirmacao['lotes']] == [
        'Depósito A',
        'Depósito B',
    ]

    importacao = post(
        client,
        '/api/leilao/importar',
        data={
            'arquivo': (BytesIO(conteudo), 'leilao.xlsx'),
            'lote_id': lote_dois['id'],
            'token_analise': resultado['token_analise'],
            'indices_aprovados': '[]',
        },
        content_type='multipart/form-data',
    )

    assert importacao.status_code == 200, importacao.get_json()
    assert importacao.get_json()['lotes_criados'] == ['Lote 03']
    estado = client.get('/api/leilao').get_json()
    assert [lote['nome'] for lote in estado['lotes']] == [
        'Depósito A',
        'Depósito B',
        'Lote 03',
    ]
    itens = estado['itens']
    assert itens[0]['lote_id'] == itens[1]['lote_id']
    assert itens[0]['lote_id'] == lote_um['id']
    assert itens[2]['lote_id'] == itens[3]['lote_id'] == lote_dois['id']
    assert itens[4]['lote_id'] != lote_dois['id']
    assert all('lote_importado' not in item for item in itens)


def test_importacao_leilao_exige_aprovacao_da_analise(client):
    importacao = post(
        client,
        '/api/leilao/importar',
        data={
            'arquivo': (
                BytesIO('Descrição;Setor\nMesa;Setor A\n'.encode()),
                'leilao.csv',
            ),
        },
        content_type='multipart/form-data',
    )

    assert importacao.status_code == 409
    assert 'analise' in importacao.get_json()['error'].lower()
    assert client.get('/api/leilao').get_json()['itens'] == []


def test_importacao_leilao_rejeita_arquivo_diferente_da_analise_aprovada(client):
    analisado = 'Descrição;Plaqueta\nMesa;123\n'.encode()
    alterado = 'Descrição;Plaqueta\nMesa;124\n'.encode()
    analise = post(
        client,
        '/api/leilao/importar/analisar',
        data={'arquivo': (BytesIO(analisado), 'leilao.csv')},
        content_type='multipart/form-data',
    )
    resultado = analise.get_json()

    importacao = post(
        client,
        '/api/leilao/importar',
        data={
            'arquivo': (BytesIO(alterado), 'leilao.csv'),
            'token_analise': resultado['token_analise'],
            'indices_aprovados': '[]',
        },
        content_type='multipart/form-data',
    )

    assert importacao.status_code == 409
    assert client.get('/api/leilao').get_json()['itens'] == []


def test_lista_leilao_importa_excel_e_inclui_item_manual(client):
    workbook = BytesIO()
    pd.DataFrame([{
        'Bem': 'Cadeira',
        'Plaqueta': '',
        'Setor atual': 'Setor B',
        'Observação': 'Precisa de reparo',
    }]).to_excel(workbook, index=False)
    importacao = importar_arquivo_leilao_aprovado(
        client,
        workbook.getvalue(),
        'leilao.xlsx',
    )
    assert importacao.status_code == 200, importacao.get_json()

    manual = post(
        client,
        '/api/leilao/manual',
        json={
            'descricao': 'Armário de aço',
            'setor': 'Setor C',
            'data': '2021-05-10',
            'valor': '250,75',
            'observacao': 'Lote 2',
        },
    )
    assert manual.status_code == 200, manual.get_json()
    itens = client.get('/api/leilao').get_json()['itens']
    assert len(itens) == 2
    assert {item['descricao'] for item in itens} == {'Cadeira', 'Armário de aço'}
    assert {item['observacao'] for item in itens} == {'Precisa de reparo', 'Lote 2'}
    assert {item['valor'] for item in itens} == {'', '250.75'}
    assert all(item['situacao'] == 'PENDENTE_IDENTIFICACAO' for item in itens)


def test_observacao_item_leilao_tem_limite_de_caracteres(client):
    resposta = post(
        client,
        '/api/leilao/manual',
        json={'descricao': 'Cadeira', 'observacao': 'x' * 2001},
    )

    assert resposta.status_code == 400
    assert 'observação' in resposta.get_json()['error'].lower()


@pytest.mark.parametrize('valor', ['-1.00', 'inválido'])
def test_inclusao_manual_leilao_rejeita_valor_invalido(client, valor):
    resposta = post(
        client,
        '/api/leilao/manual',
        json={'descricao': 'Cadeira', 'valor': valor},
    )

    assert resposta.status_code == 400
    assert client.get('/api/leilao').get_json()['total'] == 0


def test_inclusao_manual_leilao_cria_item_individual_para_cada_unidade(client):
    lote = post(client, '/api/leilao/lotes', json={'nome': 'Cadeiras'})
    lote_id = lote.get_json()['lote']['id']

    resposta = post(
        client,
        '/api/leilao/manual',
        json={
            'descricao': 'Cadeira de escritório',
            'setor': 'Setor A',
            'quantidade': 3,
            'valor': '1250,50',
            'lote_id': lote_id,
        },
    )

    assert resposta.status_code == 200, resposta.get_json()
    payload = resposta.get_json()
    assert payload['quantidade'] == 3
    assert len(payload['itens']) == 3
    assert len({item['id'] for item in payload['itens']}) == 3
    assert all(item['descricao'] == 'Cadeira de escritório' for item in payload['itens'])
    assert all(item['valor'] == '1250.50' for item in payload['itens'])
    assert all(item['lote_id'] == lote_id for item in payload['itens'])
    assert all(item['situacao'] == 'PENDENTE_IDENTIFICACAO' for item in payload['itens'])
    lista = client.get('/api/leilao').get_json()
    assert lista['total'] == 3
    assert len(lista['itens']) == 3


@pytest.mark.parametrize('quantidade', [0, -1, 1000, 1.5, True, 'abc'])
def test_inclusao_manual_leilao_rejeita_quantidade_invalida(client, quantidade):
    resposta = post(
        client,
        '/api/leilao/manual',
        json={'descricao': 'Cadeira', 'quantidade': quantidade},
    )

    assert resposta.status_code == 400
    assert 'quantidade' in resposta.get_json()['error'].lower()
    assert client.get('/api/leilao').get_json()['total'] == 0


def test_item_leilao_pode_ser_editado_e_excluido_sem_alterar_base(client, monkeypatch):
    base = pd.DataFrame([{
        'Plaqueta': '000123',
        'Descrição': 'Mesa',
        'Setor Atual': 'Setor A',
        'STATUS': 'PEND',
    }])
    monkeypatch.setattr(leilao_routes, '_carregar_base', lambda: base)

    incluido = post(
        client,
        '/api/leilao/manual',
        json={'descricao': 'Mesa antiga', 'setor': 'Setor A'},
    )
    item_id = incluido.get_json()['item']['id']
    vinculo = post(
        client,
        f'/api/leilao/{item_id}/vincular',
        json={'plaqueta': '000123'},
    )
    assert vinculo.status_code == 200

    edicao = post(
        client,
        f'/api/leilao/{item_id}/editar',
        json={
            'descricao': 'Mesa de reunião',
            'plaqueta': '123',
            'setor': 'Setor A',
            'data': '2020-01-01',
            'observacao': 'Aguardar avaliação',
            'valor': '1.234,56',
        },
    )
    assert edicao.status_code == 200, edicao.get_json()
    item_atualizado = client.get('/api/leilao').get_json()['itens'][0]
    assert item_atualizado['descricao'] == 'Mesa de reunião'
    assert item_atualizado['plaqueta_informada'] == '123'
    assert item_atualizado['data'] == '2020-01-01'
    assert item_atualizado['observacao'] == 'Aguardar avaliação'
    assert item_atualizado['valor'] == '1234.56'
    assert item_atualizado['situacao'] == 'IDENTIFICADO'
    assert item_atualizado['plaqueta_vinculada'] == '000123'
    assert base.iloc[0]['STATUS'] == 'PEND'

    exclusao = post(client, f'/api/leilao/{item_id}/excluir', json={})
    assert exclusao.status_code == 200, exclusao.get_json()
    assert 'lista de leilão' in exclusao.get_json()['mensagem']
    assert client.get('/api/leilao').get_json()['itens'] == []
    assert base.iloc[0]['STATUS'] == 'PEND'
    nao_encontrado = post(client, f'/api/leilao/{item_id}/excluir', json={})
    assert nao_encontrado.status_code == 404


def test_itens_leilao_selecionados_podem_ser_excluidos_em_grupo(client):
    lote = post(client, '/api/leilao/lotes', json={'nome': 'Móveis'})
    lote_id = lote.get_json()['lote']['id']
    incluido = post(
        client,
        '/api/leilao/manual',
        json={'descricao': 'Cadeira', 'quantidade': 3, 'lote_id': lote_id},
    )
    ids = [item['id'] for item in incluido.get_json()['itens']]

    resposta = post(
        client,
        '/api/leilao/excluir-selecionados',
        json={'ids': ids[:2]},
    )

    assert resposta.status_code == 200, resposta.get_json()
    assert resposta.get_json()['excluidos'] == 2
    estado = client.get('/api/leilao').get_json()
    assert [item['id'] for item in estado['itens']] == [ids[2]]
    assert estado['itens'][0]['lote_id'] == lote_id
    assert estado['lotes'][0]['nome'] == 'Móveis'


def test_exclusao_em_grupo_de_leilao_e_atomica_se_houver_id_invalido(client):
    incluido = post(
        client,
        '/api/leilao/manual',
        json={'descricao': 'Cadeira', 'quantidade': 2},
    )
    ids = [item['id'] for item in incluido.get_json()['itens']]

    resposta = post(
        client,
        '/api/leilao/excluir-selecionados',
        json={'ids': [ids[0], 'id-inexistente']},
    )

    assert resposta.status_code == 404
    assert [item['id'] for item in client.get('/api/leilao').get_json()['itens']] == ids


def test_lotes_leilao_organizam_itens_sem_alterar_cadastro_patrimonial(client):
    legado = [{
        'id': 'item-legado',
        'descricao': 'Mesa antiga',
        'plaqueta_informada': '',
        'setor': '',
        'data': '',
        'observacao': '',
        'situacao': 'PENDENTE_IDENTIFICACAO',
        'plaqueta_vinculada': '',
    }]
    DADOS_TRABALHO_DIR.mkdir(parents=True, exist_ok=True)
    servicos_leilao._arquivo_leilao().write_text(
        json.dumps(legado),
        encoding='utf-8',
    )

    primeiro = post(client, '/api/leilao/lotes', json={'nome': 'Móveis'})
    segundo = post(client, '/api/leilao/lotes', json={'nome': 'Equipamentos'})
    assert primeiro.status_code == 200
    assert segundo.status_code == 200
    id_primeiro = primeiro.get_json()['lote']['id']
    id_segundo = segundo.get_json()['lote']['id']

    incluido = post(
        client,
        '/api/leilao/manual',
        json={'descricao': 'Cadeira', 'lote_id': id_primeiro},
    )
    assert incluido.status_code == 200, incluido.get_json()
    id_item = incluido.get_json()['item']['id']

    itens_iniciais = client.get('/api/leilao').get_json()
    assert [lote['nome'] for lote in itens_iniciais['lotes']] == [
        'Móveis',
        'Equipamentos',
    ]
    assert itens_iniciais['itens'][0]['lote_id'] is None

    mover = post(
        client,
        f'/api/leilao/{id_item}/lote',
        json={'lote_id': id_segundo},
    )
    assert mover.status_code == 200
    remover = post(
        client,
        f'/api/leilao/{id_item}/lote',
        json={'lote_id': None},
    )
    assert remover.status_code == 200
    assert client.get('/api/leilao').get_json()['itens'][1]['lote_id'] is None

    renomear = post(
        client,
        f'/api/leilao/lotes/{id_segundo}/editar',
        json={'nome': 'Eletrônicos'},
    )
    assert renomear.status_code == 200
    reordenar = post(
        client,
        '/api/leilao/lotes/reordenar',
        json={'ids': [id_segundo, id_primeiro]},
    )
    assert reordenar.status_code == 200
    lotes_ordenados = client.get('/api/leilao').get_json()['lotes']
    assert [(lote['numero'], lote['nome']) for lote in lotes_ordenados] == [
        (1, 'Eletrônicos'),
        (2, 'Móveis'),
    ]

    excluir_com_item = post(
        client,
        f'/api/leilao/lotes/{id_primeiro}/excluir',
        json={},
    )
    assert excluir_com_item.status_code == 200
    excluir_item = post(client, f'/api/leilao/{id_item}/excluir', json={})
    assert excluir_item.status_code == 200
    estado = client.get('/api/leilao').get_json()
    assert [item['id'] for item in estado['itens']] == ['item-legado']
    assert estado['itens'][0]['descricao'] == 'Mesa antiga'
    assert estado['lotes'][0]['nome'] == 'Eletrônicos'


def test_lotes_leilao_podem_ter_valor_total(client):
    criado = post(
        client,
        '/api/leilao/lotes',
        json={'nome': 'Móveis', 'valor': '1.234,56'},
    )
    assert criado.status_code == 200
    lote = criado.get_json()['lote']
    assert lote['valor'] == '1234.56'

    estado = client.get('/api/leilao').get_json()
    assert estado['lotes'][0]['valor'] == '1234.56'

    editado = post(
        client,
        f'/api/leilao/lotes/{lote["id"]}/editar',
        json={'nome': 'Móveis comerciais', 'valor': '2500'},
    )
    assert editado.status_code == 200
    assert editado.get_json()['lote']['valor'] == '2500.00'

    invalido = post(
        client,
        f'/api/leilao/lotes/{lote["id"]}/editar',
        json={'nome': 'Móveis comerciais', 'valor': '-10'},
    )
    assert invalido.status_code == 400
    assert 'zero ou maior' in invalido.get_json()['error'].lower()


def test_mover_item_atualiza_valores_manuais_dos_lotes(client):
    origem = post(
        client,
        '/api/leilao/lotes',
        json={'nome': 'Origem', 'valor': '1000'},
    ).get_json()['lote']
    destino = post(
        client,
        '/api/leilao/lotes',
        json={'nome': 'Destino', 'valor': '500'},
    ).get_json()['lote']
    incluido = post(
        client,
        '/api/leilao/manual',
        json={
            'descricao': 'Mesa',
            'valor': '250',
            'quantidade': 1,
            'lote_id': origem['id'],
        },
    )
    assert incluido.status_code == 200
    item_id = incluido.get_json()['item']['id']
    estado = client.get('/api/leilao').get_json()
    assert next(lote for lote in estado['lotes'] if lote['id'] == origem['id'])['valor'] == '1250.00'
    assert next(lote for lote in estado['lotes'] if lote['id'] == destino['id'])['valor'] == '500.00'

    movido = post(
        client,
        f'/api/leilao/{item_id}/lote',
        json={'lote_id': destino['id']},
    )
    assert movido.status_code == 200
    estado = client.get('/api/leilao').get_json()
    assert next(lote for lote in estado['lotes'] if lote['id'] == origem['id'])['valor'] == '1000.00'
    assert next(lote for lote in estado['lotes'] if lote['id'] == destino['id'])['valor'] == '750.00'


def test_lotes_leilao_rejeitam_nomes_repetidos_ao_criar_e_renomear(client):
    primeiro = post(client, '/api/leilao/lotes', json={'nome': 'Móveis'})
    assert primeiro.status_code == 200
    primeiro_id = primeiro.get_json()['lote']['id']

    duplicado = post(client, '/api/leilao/lotes', json={'nome': ' moveis '})
    assert duplicado.status_code == 409
    assert 'já existe' in duplicado.get_json()['error'].lower()

    segundo = post(client, '/api/leilao/lotes', json={'nome': 'Equipamentos'})
    assert segundo.status_code == 200
    segundo_id = segundo.get_json()['lote']['id']
    renomear = post(
        client,
        f'/api/leilao/lotes/{segundo_id}/editar',
        json={'nome': 'MÓVEIS'},
    )
    assert renomear.status_code == 409
    assert [
        lote['nome'] for lote in client.get('/api/leilao').get_json()['lotes']
    ] == ['Móveis', 'Equipamentos']
    assert client.get('/api/leilao').get_json()['lotes'][0]['id'] == primeiro_id


def test_importacao_de_leilao_pode_adicionar_itens_diretamente_a_um_lote(client):
    lote = post(client, '/api/leilao/lotes', json={'nome': 'Lote importado'})
    lote_id = lote.get_json()['lote']['id']
    importacao = importar_arquivo_leilao_aprovado(
        client,
        'Descrição;Setor\nCadeira;Setor A\n'.encode(),
        'leilao.csv',
        lote_id,
    )

    assert importacao.status_code == 200, importacao.get_json()
    itens = client.get('/api/leilao').get_json()['itens']
    assert len(itens) == 1
    assert itens[0]['lote_id'] == lote_id


def test_rota_entrega_miniatura_redimensionada(client):
    FOTOS_DIR.mkdir(parents=True, exist_ok=True)
    caminho = FOTOS_DIR / 'miniatura_teste.png'
    Image.new('RGB', (300, 150), color=(20, 40, 60)).save(caminho)

    resposta = client.get('/dados-web/fotos/miniatura_teste.png?miniatura=1')

    assert resposta.status_code == 200
    assert resposta.mimetype == 'image/jpeg'
    with Image.open(BytesIO(resposta.data)) as miniatura:
        assert miniatura.size == (96, 48)


def test_relatorio_gerencial_e_backup_web_restauravel(client):
    assert preparar_base(client).status_code == 200
    relatorio = post(client, '/api/relatorios/pendencias', json={})
    assert relatorio.status_code == 200, relatorio.get_json()
    nome_relatorio = relatorio.get_json()['arquivo']
    resposta_relatorio = client.get(f'/api/relatorios/{nome_relatorio}')
    assert resposta_relatorio.status_code == 200
    dados_relatorio = resposta_relatorio.data
    resposta_relatorio.close()
    workbook_relatorio = load_workbook(BytesIO(dados_relatorio))
    assert all(
        planilha.oddHeader.right.text == 'F3 Tecnologia'
        for planilha in workbook_relatorio.worksheets
    )
    workbook_relatorio.close()

    item = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens'][0]
    assert post(
        client,
        f"/api/itens/{item['id']}/editar",
        json={'status': 'OK', 'estado': 'EM USO', 'observacao': 'Estado salvo'},
    ).status_code == 200
    backup = post(client, '/api/backups', json={})
    assert backup.status_code == 200
    nome_backup = backup.get_json()['arquivo']

    assert post(
        client,
        f"/api/itens/{item['id']}/editar",
        json={'status': 'BAIXA', 'estado': 'EM USO', 'observacao': 'Mudança posterior'},
    ).status_code == 200
    restauracao = post(
        client,
        f'/api/backups/{nome_backup}/restaurar',
        json={'confirmado': True},
    )
    assert restauracao.status_code == 200, restauracao.get_json()

    restaurado = client.get('/api/conferencia?setor=Setor%20A').get_json()['itens'][0]
    assert restaurado['STATUS'] == 'OK'
    assert restaurado['OBSERVACAO'] == 'Estado salvo'
    assert list(BACKUPS_DIR.glob('backup_web_*.zip'))


def test_relatorios_da_busca_exportam_exatamente_os_filtros_aplicados(client):
    assert preparar_base(client).status_code == 200
    filtros = {
        'termo': '',
        'setores': ['Setor A'],
        'status': ['PEND'],
        'foto': [],
        'data_inicial': '',
        'data_final': '',
    }

    excel = post(client, '/api/relatorios/busca/excel', json=filtros)
    assert excel.status_code == 200, excel.get_json()
    assert excel.get_json()['total'] == 1
    resposta_excel = client.get(
        f"/api/relatorios/{excel.get_json()['arquivo']}"
    )
    assert resposta_excel.status_code == 200
    dados_excel = resposta_excel.data
    resposta_excel.close()
    workbook = load_workbook(BytesIO(dados_excel))
    assert 'Itens_Filtrados' in workbook.sheetnames
    assert all(
        planilha.oddHeader.right.text == 'F3 Tecnologia'
        for planilha in workbook.worksheets
    )
    workbook.close()

    pdf = post(client, '/api/relatorios/busca/pdf', json=filtros)
    assert pdf.status_code == 200, pdf.get_json()
    resposta_pdf = client.get(f"/api/relatorios/{pdf.get_json()['arquivo']}")
    assert resposta_pdf.status_code == 200
    assert resposta_pdf.data.startswith(b'%PDF')

    vazio = post(
        client,
        '/api/relatorios/busca/excel',
        json={**filtros, 'termo': 'texto sem correspondência'},
    )
    assert vazio.status_code == 400


def test_relatorio_excel_da_busca_inclui_todas_as_colunas_originais_sh3(client):
    csv_sh3 = (
        'Código;Plaqueta;Descrição;Modelo;Setor Atual;Data;Fabricante SH3;Valor Atual\n'
        '001;000123;Mesa original;M1;Setor A;01/02/2024;Fabricante X;900,00\n'
    )
    assert enviar_csv(client, conteudo=csv_sh3).status_code == 200
    resultado = criar_resultado_excel([{
        'Código': '001',
        'Plaqueta': '000123',
        'Descrição': 'Mesa conferida',
        'Setor Atual': 'Setor B',
        'Data': '05/03/2025',
        'STATUS': 'PEND',
    }])
    assert enviar_resultado(client, resultado).status_code == 200

    excel = post(client, '/api/relatorios/busca/excel', json={})
    assert excel.status_code == 200, excel.get_json()
    resposta = client.get(f"/api/relatorios/{excel.get_json()['arquivo']}")
    workbook = load_workbook(BytesIO(resposta.data), data_only=True)
    planilha = workbook['Itens_Filtrados']
    colunas = {celula.value: celula.column for celula in planilha[1]}

    assert planilha.cell(2, colunas['Descrição']).value == 'Mesa conferida'
    assert planilha.cell(2, colunas['Setor Atual']).value == 'Setor B'
    assert planilha.cell(2, colunas['SH3 - Código']).value == '001'
    assert planilha.cell(2, colunas['SH3 - Plaqueta']).value == '000123'
    assert planilha.cell(2, colunas['SH3 - Descrição']).value == 'Mesa original'
    assert planilha.cell(2, colunas['SH3 - Setor Atual']).value == 'Setor A'
    assert planilha.cell(2, colunas['SH3 - Data']).value == '01/02/2024'
    assert planilha.cell(2, colunas['Fabricante SH3']).value == 'Fabricante X'
    assert planilha.cell(2, colunas['Valor Atual']).value == '900,00'

    workbook.close()
    resposta.close()


def test_restauracao_exige_confirmacao_e_backup_web_valido(client):
    assert preparar_base(client).status_code == 200
    backup = post(client, '/api/backups', json={}).get_json()['arquivo']

    sem_confirmacao = post(
        client,
        f'/api/backups/{backup}/restaurar',
        json={'confirmado': False},
    )
    assert sem_confirmacao.status_code == 400

    arquivo_desktop = BACKUPS_DIR / 'backup_web_invalido.zip'
    arquivo_desktop.write_bytes(b'nao e um zip')
    invalido = post(
        client,
        '/api/backups/backup_web_invalido.zip/restaurar',
        json={'confirmado': True},
    )
    assert invalido.status_code == 400

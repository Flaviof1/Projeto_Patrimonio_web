import csv
import json

import pandas as pd
import pytest

import migrar_mysql
from migrar_mysql import _validar_copia
from mysql_store import MySQLStore, mysql_ativo


def test_mysql_serializa_campos_de_consulta_e_preserva_a_linha_completa():
    dados = pd.DataFrame([{
        'Código': 48,
        'Plaqueta': '000048',
        'Descrição': 'Arquivo de aço',
        'Setor Atual': 'SETOR ARRECADAÇÃO',
        'STATUS': 'OK',
        'ESTADO': 'EM USO',
        'OBSERVACAO': 'Conferido',
        'Foto': 'foto.png',
        'Classificação': 2101011001,
    }], index=[41])

    colunas, linhas = MySQLStore._rows(dados)
    valores = dict(zip(colunas, linhas[0]))
    completo = json.loads(valores['dados_completos'])

    assert valores['id'] == 41
    assert valores['plaqueta'] == '000048'
    assert valores['setor_atual'] == 'SETOR ARRECADAÇÃO'
    assert valores['status'] == 'OK'
    assert valores['foto'] == 'foto.png'
    assert completo == dados.iloc[0].to_dict()


def test_mysql_rejeita_identificadores_duplicados():
    dados = pd.DataFrame(
        [{'Plaqueta': '000001'}, {'Plaqueta': '000002'}],
        index=[7, 7],
    )

    with pytest.raises(ValueError, match='identificadores internos duplicados'):
        MySQLStore._rows(dados)


def test_migracao_compara_json_sem_depender_da_ordem_das_chaves():
    original = pd.DataFrame([{
        'Plaqueta': '000048',
        'Descrição': 'Arquivo',
        'Setor Atual': 'Arrecadação',
        'STATUS': 'OK',
        'ESTADO': 'EM USO',
        'OBSERVACAO': '',
    }], index=[41])
    recarregado = original[list(reversed(original.columns))]

    class Banco:
        def load(self):
            return recarregado

    _validar_copia(Banco(), original)


def test_migracao_detecta_diferenca_sem_escrever_no_banco():
    original = pd.DataFrame([{
        'Plaqueta': '000048',
        'Setor Atual': 'Arrecadação',
        'STATUS': 'OK',
    }], index=[41])
    diferente = original.copy()
    diferente.at[41, 'Setor Atual'] = 'Outro setor'

    class Banco:
        def load(self):
            return diferente

    with pytest.raises(ValueError, match='conteúdo diferente'):
        _validar_copia(Banco(), original)


def test_migracao_valida_tabela_existente_sem_importar_app(monkeypatch, tmp_path):
    dados = pd.DataFrame([{
        'Plaqueta': '000048',
        'Setor Atual': 'Arrecadação',
        'STATUS': 'OK',
    }])
    fonte = tmp_path / 'conferencia_resultado.xlsx'
    dados.to_excel(fonte, index=False)

    class BancoExistente:
        host = '127.0.0.1'
        port = 3306
        database = 'patrimonio_web'

        def count(self):
            return 1

        def load(self):
            return dados.copy()

        def replace_all(self, dataframe):
            pytest.fail('A validação não deve sobrescrever a tabela existente.')

    banco = BancoExistente()
    monkeypatch.setattr(migrar_mysql, '_arquivo_fonte', lambda: fonte)
    monkeypatch.setattr(
        migrar_mysql.MySQLStore,
        'from_environment',
        classmethod(lambda cls: banco),
    )

    assert migrar_mysql.migrar() is True


def test_comparacao_mysql_consulta_apenas_a_tabela_sem_alterar_schema(monkeypatch):
    class Cursor:
        consulta = None
        fechado = False

        def execute(self, consulta):
            self.consulta = consulta

        def fetchall(self):
            return [(3, {'Plaqueta': '000048', 'Setor Atual': 'Arrecadação'})]

        def close(self):
            self.fechado = True

    class Conexao:
        def __init__(self):
            self.cursor_criado = Cursor()
            self.fechada = False

        def cursor(self):
            return self.cursor_criado

        def close(self):
            self.fechada = True

    class Connector:
        Error = Exception

    conexao = Conexao()
    banco = type('Banco', (), {'_connect': lambda self: conexao})()
    monkeypatch.setattr(
        MySQLStore,
        '_connector',
        staticmethod(lambda: Connector),
    )

    dados = migrar_mysql._carregar_mysql_somente_leitura(banco)

    assert dados.loc[3, 'Plaqueta'] == '000048'
    assert conexao.cursor_criado.consulta.startswith('SELECT ')
    assert conexao.cursor_criado.fechado is True
    assert conexao.fechada is True


def test_comparacao_excel_mysql_gera_relatorio_de_diferencas_sem_escrever_nas_bases(
    monkeypatch, tmp_path
):
    excel = pd.DataFrame([
        {
            'Plaqueta': '1',
            'Setor Atual': 'Setor A',
            'STATUS': 'OK',
            'Data': '01/01/2024',
        },
        {
            'Plaqueta': '000002',
            'Setor Atual': 'Setor B',
            'STATUS': 'PEND',
            'Data': '05/01/2024',
        },
        {
            'Plaqueta': '000003',
            'Setor Atual': 'Setor C',
            'STATUS': 'OK',
            'Data': '07/01/2024',
        },
    ])
    mysql = pd.DataFrame(
        [
            {
                'Plaqueta': '000001',
                'Setor Atual': 'Setor A',
                'STATUS': 'OK',
                'Data': '01/01/2024',
            },
            {
                'Plaqueta': '000002',
                'Setor Atual': 'Setor B',
                'STATUS': 'BAIXA',
                'Data': '06/01/2024',
            },
            {
                'Plaqueta': '000004',
                'Setor Atual': 'Setor D',
                'STATUS': 'OK',
                'Data': '08/01/2024',
            },
        ],
        index=[0, 1, 3],
    )
    fonte = tmp_path / 'conferencia_resultado.xlsx'
    excel.to_excel(fonte, index=False)
    monkeypatch.setattr(migrar_mysql, '_arquivo_fonte', lambda: fonte)
    monkeypatch.setattr(
        migrar_mysql.MySQLStore,
        'from_environment',
        classmethod(lambda cls: object()),
    )
    monkeypatch.setattr(
        migrar_mysql,
        '_carregar_mysql_somente_leitura',
        lambda banco: mysql.copy(),
    )
    import paths

    monkeypatch.setattr(paths, 'RELATORIOS_DIR', tmp_path / 'relatorios')

    resumo, relatorio = migrar_mysql.comparar()

    assert resumo['somente_excel'] == 1
    assert resumo['somente_mysql'] == 1
    assert resumo['conteudo_diferente'] == 1
    assert resumo['plaquetas_correspondentes'] == 2
    assert resumo['campos_diferentes'] == {'Data': 1, 'STATUS': 1}
    with relatorio.open(encoding='utf-8-sig', newline='') as arquivo:
        linhas = list(csv.DictReader(arquivo, delimiter=';'))
    assert {linha['tipo'] for linha in linhas} == {
        'somente_excel',
        'somente_mysql',
        'conteudo_diferente',
    }
    divergencia = next(linha for linha in linhas if linha['tipo'] == 'conteudo_diferente')
    assert divergencia['plaqueta'] == '000002'
    assert divergencia['data_excel'] == '05/01/2024'
    assert divergencia['data_mysql'] == '06/01/2024'


def test_comparacao_por_plaqueta_separa_duplicadas_e_sem_plaqueta():
    excel = pd.DataFrame([
        {'Plaqueta': '1', 'Setor Atual': 'Setor A'},
        {'Plaqueta': '000001', 'Setor Atual': 'Setor B'},
        {'Plaqueta': '', 'Identificador Provisório': 'SEM-ID-001'},
    ])
    mysql = pd.DataFrame(
        [
            {'Plaqueta': '000001', 'Setor Atual': 'Setor A'},
            {'Plaqueta': '', 'Identificador Provisório': 'SEM-ID-002'},
        ],
        index=[5, 6],
    )

    resumo, linhas = migrar_mysql._comparar_bases(excel, mysql)

    assert resumo['plaquetas_duplicadas'] == 1
    assert resumo['sem_plaqueta_excel'] == 1
    assert resumo['sem_plaqueta_mysql'] == 1
    assert resumo['plaquetas_correspondentes'] == 0
    assert {linha['tipo'] for linha in linhas} == {
        'plaqueta_duplicada',
        'sem_plaqueta_excel',
        'sem_plaqueta_mysql',
    }


def test_mysql_ativo_valida_o_modo_configurado(monkeypatch):
    monkeypatch.setenv('PATRIMONIO_STORAGE', 'mysql')
    assert mysql_ativo()

    monkeypatch.setenv('PATRIMONIO_STORAGE', 'excel')
    assert not mysql_ativo()

    monkeypatch.setenv('PATRIMONIO_STORAGE', 'desconhecido')
    with pytest.raises(ValueError, match='PATRIMONIO_STORAGE'):
        mysql_ativo()


def test_configuracao_mysql_usa_padrao_local_de_banco_e_usuario(monkeypatch):
    monkeypatch.delenv('PATRIMONIO_MYSQL_DATABASE', raising=False)
    monkeypatch.delenv('PATRIMONIO_MYSQL_USER', raising=False)

    banco = MySQLStore.from_environment()

    assert banco.host == '127.0.0.1'
    assert banco.database == 'patrimonio_web'
    assert banco.user == 'patrimonio_app'

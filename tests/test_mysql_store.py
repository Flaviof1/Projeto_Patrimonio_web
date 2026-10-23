import json

import pandas as pd
import pytest

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

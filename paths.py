from pathlib import Path
import datetime
import os
import shutil
import json

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get(
    'PATRIMONIO_WEB_DATA_DIR', BASE_DIR / 'dados_web'
)).resolve()

DADOS_ENTRADA_DIR = DATA_DIR / 'dados_entrada'
DADOS_TRABALHO_DIR = DATA_DIR / 'dados_trabalho'
RELATORIOS_DIR = DATA_DIR / 'relatorios'
DOCS_DIR = DATA_DIR / 'docs'
BACKUPS_DIR = DATA_DIR / 'backups'

PASTAS_PROJETO = [
    DADOS_ENTRADA_DIR,
    DADOS_TRABALHO_DIR,
    RELATORIOS_DIR,
    BACKUPS_DIR,
]

ARQUIVO_CONTROLE = DADOS_TRABALHO_DIR / '.last_preparation'
ARQUIVO_CONTADOR = DADOS_TRABALHO_DIR / '.contador_alteracoes.json'

# Arquivos de backup circular
BACKUP_ATUAL = DADOS_TRABALHO_DIR / 'conferencia_resultado_backup_atual.xlsx'
BACKUP_ANTERIOR = DADOS_TRABALHO_DIR / \
    'conferencia_resultado_backup_anterior.xlsx'
VERSOES_RESULTADO_DIR = DADOS_TRABALHO_DIR / 'versoes_resultado'
FOTOS_DIR = DADOS_TRABALHO_DIR / 'Fotos'

# Arquivos de trabalho da conferência (adicionados)
ARQUIVO_RESULTADO = str(DADOS_TRABALHO_DIR / 'conferencia_resultado.xlsx')
ARQUIVO_CAMPOS = str(DADOS_TRABALHO_DIR / 'conferencia_campo.xlsx')


def garantir_pastas():
    for pasta in PASTAS_PROJETO:
        pasta.mkdir(parents=True, exist_ok=True)
    VERSOES_RESULTADO_DIR.mkdir(parents=True, exist_ok=True)
    FOTOS_DIR.mkdir(parents=True, exist_ok=True)


def caminho_entrada(nome):
    return str(DADOS_ENTRADA_DIR / nome)


def caminho_trabalho(nome):
    return str(DADOS_TRABALHO_DIR / nome)


def caminho_relatorio(nome):
    return str(RELATORIOS_DIR / nome)


def caminho_doc(nome):
    return str(DOCS_DIR / nome)


def localizar_arquivo(nome, pasta_preferida):
    caminho_preferido = pasta_preferida / nome
    if caminho_preferido.exists():
        return str(caminho_preferido)

    nome_normalizado = nome.replace('_', '').replace(' ', '').lower()
    for candidato in pasta_preferida.iterdir() if pasta_preferida.exists() else []:
        if candidato.is_file():
            candidato_normalizado = candidato.name.replace(
                '_', '').replace(' ', '').lower()
            if candidato_normalizado == nome_normalizado:
                return str(candidato)

    caminho_raiz = BASE_DIR / nome
    if caminho_raiz.exists():
        return str(caminho_raiz)

    return str(caminho_preferido)


def salvar_timestamp_preparacao():
    data_hora = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    with open(ARQUIVO_CONTROLE, 'w') as f:
        f.write(data_hora)
    return data_hora


def ler_timestamp_preparacao():
    if not ARQUIVO_CONTROLE.exists():
        return None
    try:
        with open(ARQUIVO_CONTROLE, 'r') as f:
            return f.read().strip()
    except:
        return None


def calcular_dias_desde_preparacao():
    timestamp = ler_timestamp_preparacao()
    if not timestamp:
        return None

    try:
        data_preparacao = datetime.datetime.strptime(
            timestamp, '%d/%m/%Y %H:%M:%S')
        data_atual = datetime.datetime.now()
        diferenca = (data_atual - data_preparacao).days
        return diferenca
    except:
        return None


def incrementar_contador():
    """Incrementa contador de alterações e retorna o total"""
    try:
        if ARQUIVO_CONTADOR.exists():
            with open(ARQUIVO_CONTADOR, 'r') as f:
                dados = json.load(f)
                contador = dados.get('contador', 0) + 1
        else:
            contador = 1

        with open(ARQUIVO_CONTADOR, 'w') as f:
            json.dump({'contador': contador}, f)

        return contador
    except:
        return 0


def resetar_contador():
    """Reseta o contador de alterações"""
    try:
        with open(ARQUIVO_CONTADOR, 'w') as f:
            json.dump({'contador': 0}, f)
    except:
        pass


def criar_backup_circular(forcar=False):
    """
    Cria backup circular mantendo apenas:
    - backup_atual.xlsx (mais recente)
    - backup_anterior.xlsx (penúltimo)

    Args:
        forcar: Se True, força o backup independente do contador
    """
    arquivo_principal = DADOS_TRABALHO_DIR / 'conferencia_resultado.xlsx'

    if not arquivo_principal.exists():
        return None

    # Verifica se deve fazer backup (a cada 10 alterações ou forçado)
    if not forcar:
        contador = 0
        try:
            if ARQUIVO_CONTADOR.exists():
                with open(ARQUIVO_CONTADOR, 'r') as f:
                    dados = json.load(f)
                    contador = dados.get('contador', 0)
        except:
            pass

        # Só faz backup a cada 10 alterações
        if contador < 10:
            return None

    try:
        # PASSO 1: Backup atual vira anterior (sobrescreve)
        if BACKUP_ATUAL.exists():
            shutil.move(str(BACKUP_ATUAL), str(BACKUP_ANTERIOR))

        # PASSO 2: Copia arquivo principal para backup atual
        shutil.copy2(arquivo_principal, BACKUP_ATUAL)

        # Resetar contador
        resetar_contador()

        return str(BACKUP_ATUAL)

    except Exception as e:
        print(f"Erro ao criar backup: {e}")
        return None


def criar_backup_ao_fechar():
    """Cria backup ao fechar o programa"""
    return criar_backup_circular(forcar=True)


def criar_versao_resultado(arquivo_principal=None):
    """Cria uma cópia versionada da planilha de resultado."""
    arquivo_principal = arquivo_principal or (
        DADOS_TRABALHO_DIR / 'conferencia_resultado.xlsx')

    if not arquivo_principal.exists():
        return None

    try:
        VERSOES_RESULTADO_DIR.mkdir(parents=True, exist_ok=True)
        data_hora = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
        arquivo_versao = VERSOES_RESULTADO_DIR / \
            f'conferencia_resultado_{data_hora}.xlsx'
        shutil.copy2(arquivo_principal, arquivo_versao)
        return str(arquivo_versao)
    except Exception as e:
        print(f"Erro ao criar versão da planilha: {e}")
        return None


def restaurar_backup(versao='atual'):
    """
    Restaura um backup

    Args:
        versao: 'atual' ou 'anterior'

    Returns:
        str: Mensagem de sucesso ou erro
    """
    arquivo_backup = BACKUP_ATUAL if versao == 'atual' else BACKUP_ANTERIOR
    arquivo_principal = DADOS_TRABALHO_DIR / 'conferencia_resultado.xlsx'

    if not arquivo_backup.exists():
        return f"❌ Backup {versao} não encontrado!"

    try:
        # Criar backup temporário do arquivo atual antes de restaurar
        if arquivo_principal.exists():
            backup_temp = DADOS_TRABALHO_DIR / 'conferencia_resultado_temp.xlsx'
            shutil.copy2(arquivo_principal, backup_temp)

        # Restaurar backup
        shutil.copy2(arquivo_backup, arquivo_principal)

        # Remover arquivo temporário
        temp_file = DADOS_TRABALHO_DIR / 'conferencia_resultado_temp.xlsx'
        if temp_file.exists():
            temp_file.unlink()

        return f"✅ Backup {versao} restaurado com sucesso!"

    except Exception as e:
        return f"❌ Erro ao restaurar: {e}"


def listar_backups():
    """Retorna informações sobre os backups disponíveis"""
    backups = []

    if BACKUP_ATUAL.exists():
        stats = BACKUP_ATUAL.stat()
        data = datetime.datetime.fromtimestamp(stats.st_mtime)
        tamanho = stats.st_size / 1024
        backups.append({
            'versao': 'Atual',
            'arquivo': str(BACKUP_ATUAL.name),
            'data': data.strftime('%d/%m/%Y %H:%M:%S'),
            'tamanho': f'{tamanho:.1f} KB'
        })

    if BACKUP_ANTERIOR.exists():
        stats = BACKUP_ANTERIOR.stat()
        data = datetime.datetime.fromtimestamp(stats.st_mtime)
        tamanho = stats.st_size / 1024
        backups.append({
            'versao': 'Anterior',
            'arquivo': str(BACKUP_ANTERIOR.name),
            'data': data.strftime('%d/%m/%Y %H:%M:%S'),
            'tamanho': f'{tamanho:.1f} KB'
        })

    return backups

import difflib
import hashlib
import json
import os
import pandas as pd
import re
import unicodedata
import zipfile
from decimal import (
    Decimal,
    InvalidOperation,
)
from datetime import (
    date,
    datetime,
)
from flask import (
    abort,
)
from io import (
    BytesIO,
)
from pathlib import (
    Path,
)
from paths import (
    DADOS_TRABALHO_DIR,
)
from preparar_dados import (
    formatar_plaqueta_exibicao,
)
from secrets import (
    token_hex,
)
from werkzeug.utils import (
    secure_filename,
)
from servicos_comuns import (
    _normalizar_valor_leilao,
)

STATUS_DIVERGENTE = frozenset({'DIVERG', 'DIV', 'DIVERGENTE'})


def _salvar_estado_leilao(estado):
    caminho = _arquivo_leilao()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix('.json.tmp')
    try:
        temporario.write_text(
            json.dumps(estado, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        os.replace(temporario, caminho)
    except OSError:
        temporario.unlink(missing_ok=True)
        raise

def _carregar_estado_leilao():
    caminho = _arquivo_leilao()
    if not caminho.exists():
        return {'itens': [], 'lotes': []}
    try:
        dados = json.loads(caminho.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError('Não foi possível ler a lista de itens para leilão.') from error
    if isinstance(dados, list):
        itens, lotes = dados, []
    elif isinstance(dados, dict):
        itens, lotes = dados.get('itens'), dados.get('lotes')
    else:
        itens, lotes = None, None
    if (
        not isinstance(itens, list)
        or any(not isinstance(item, dict) for item in itens)
        or not isinstance(lotes, list)
        or any(not isinstance(lote, dict) for lote in lotes)
    ):
        raise ValueError('A lista de itens para leilão está em formato inválido.')
    ids_lotes = [lote.get('id') for lote in lotes]
    if (
        any(not isinstance(identificador, str) or not identificador for identificador in ids_lotes)
        or len(ids_lotes) != len(set(ids_lotes))
    ):
        raise ValueError('A lista de lotes para leilão está em formato inválido.')
    for lote in lotes:
        lote.setdefault('valor', '')
    for item in itens:
        item.setdefault('lote_id', None)
        item.setdefault('valor', '')
        if item['lote_id'] is not None and item['lote_id'] not in ids_lotes:
            raise ValueError('Um item está vinculado a um lote inexistente.')
    return {'itens': itens, 'lotes': lotes}

def _ler_arquivo_leilao(arquivo):
    nome = secure_filename(arquivo.filename or '')
    extensao = Path(nome).suffix.lower()
    if extensao not in {'.xlsx', '.csv'}:
        raise ValueError('Envie a lista em formato Excel (.xlsx) ou CSV (.csv).')
    conteudo = arquivo.read()
    if not conteudo:
        raise ValueError('O arquivo da lista está vazio.')
    if len(conteudo) > 20 * 1024 * 1024:
        raise ValueError('O arquivo da lista excede o limite de 20 MB.')
    try:
        buffer = BytesIO(conteudo)
        if extensao == '.xlsx':
            planilha = pd.read_excel(buffer, dtype=object)
        else:
            try:
                planilha = pd.read_csv(
                    buffer, sep=None, engine='python', dtype=object, encoding='utf-8-sig'
                )
            except UnicodeDecodeError:
                buffer.seek(0)
                planilha = pd.read_csv(
                    buffer, sep=None, engine='python', dtype=object, encoding='latin1'
                )
    except (OSError, ValueError, ImportError, zipfile.BadZipFile, pd.errors.ParserError) as error:
        raise ValueError(f'Não foi possível ler a lista enviada: {error}') from error
    if planilha.empty:
        raise ValueError('A lista enviada não contém itens.')

    aliases = {
        'lote': {
            'lote', 'lotes', 'nome lote', 'lote nome', 'numero lote',
            'numero do lote', 'n lote', 'lote numero', 'lote n', 'lote no',
        },
        'plaqueta': {'plaqueta', 'numero plaqueta', 'n plaqueta', 'tombamento'},
        'descricao': {
            'descricao', 'bem descricao', 'descricao bem', 'bem', 'nome bem',
            'item', 'descricao do bem', 'bem descricao item',
        },
        'setor': {'setor', 'setor atual', 'local', 'localizacao', 'unidade'},
        'data': {'data', 'data aquisicao', 'data de aquisicao', 'data cadastro'},
        'valor': {
            'valor', 'valor unitario', 'valor do item', 'preco', 'preco unitario',
            'valor estimado',
        },
        'observacao': {
            'observacao', 'observacoes', 'observacao do item', 'observacoes do item',
            'observacao item', 'observacoes item',
        },
    }
    colunas = {}
    for coluna in planilha.columns:
        normalizado = _normalizar_cabecalho_leilao(coluna)
        for destino, nomes in aliases.items():
            if normalizado in nomes:
                colunas.setdefault(destino, coluna)
    if 'descricao' not in colunas and 'plaqueta' not in colunas:
        raise ValueError(
            'A planilha precisa ter uma coluna de descrição ou plaqueta.'
        )

    def valor_celula(linha, campo):
        if campo not in colunas:
            return ''
        valor = linha.get(colunas[campo], '')
        if pd.isna(valor):
            return ''
        if isinstance(valor, (date, datetime, pd.Timestamp)):
            return valor.strftime('%Y-%m-%d')
        return str(valor).strip()

    itens = []
    for numero_linha, (_, linha) in enumerate(planilha.iterrows(), start=2):
        descricao = valor_celula(linha, 'descricao')
        plaqueta_informada = valor_celula(linha, 'plaqueta')[:100]
        if not descricao and not plaqueta_informada:
            continue
        descricao_provisoria = not bool(descricao)
        if descricao_provisoria:
            descricao = f'Item com plaqueta {plaqueta_informada}'
        lote_importado = (
            valor_celula(linha, 'lote') if 'lote' in colunas else None
        )
        if lote_importado is not None and len(lote_importado) > 80:
            raise ValueError(
                f'O lote na linha {numero_linha} excede o limite de 80 caracteres.'
            )
        try:
            valor = _normalizar_valor_leilao(valor_celula(linha, 'valor'))
        except ValueError as error:
            raise ValueError(f'Valor inválido na linha {numero_linha}: {error}') from error
        itens.append({
            'id': token_hex(12),
            'plaqueta_informada': plaqueta_informada,
            'descricao': descricao[:500],
            'descricao_provisoria': descricao_provisoria,
            'setor': valor_celula(linha, 'setor')[:255],
            'data': valor_celula(linha, 'data')[:50],
            'valor': valor,
            'observacao': valor_celula(linha, 'observacao')[:2000],
            'situacao': 'PENDENTE_IDENTIFICACAO',
            'plaqueta_vinculada': '',
            'lote_importado': lote_importado,
        })
    if not itens:
        raise ValueError('A lista não contém linhas com descrição ou plaqueta.')
    return itens

def _sugerir_correspondencias(item, dados):
    if dados is None or 'Plaqueta' not in dados.columns or 'Descrição' not in dados.columns:
        return []
    descricao = _normalizar_texto_correspondencia(item.get('descricao'))
    plaqueta_informada = str(item.get('plaqueta_informada', '')).strip()
    setor_informado = _normalizar_texto_correspondencia(item.get('setor'))
    candidatos = []
    for indice, bem in dados.iterrows():
        status = str(bem.get('STATUS', '')).strip().upper()
        if status == 'OK':
            continue
        plaqueta = formatar_plaqueta_exibicao(bem.get('Plaqueta', ''))
        descricao_base = _normalizar_texto_correspondencia(bem.get('Descrição', ''))
        similaridade = difflib.SequenceMatcher(None, descricao, descricao_base).ratio()
        if descricao and descricao in descricao_base:
            similaridade = max(similaridade, 0.85)
        if plaqueta_informada and plaqueta_informada == plaqueta:
            similaridade = 1.0
        setor_base = _normalizar_texto_correspondencia(bem.get('Setor Atual', ''))
        if setor_informado and setor_informado == setor_base:
            similaridade = min(1.0, similaridade + 0.1)
        if similaridade < 0.35:
            continue
        candidatos.append({
            'id': int(indice),
            'plaqueta': plaqueta,
            'descricao': str(bem.get('Descrição', '') or ''),
            'setor': str(bem.get('Setor Atual', '') or ''),
            'similaridade': round(similaridade * 100),
            'status': status,
        })
    candidatos.sort(key=lambda candidato: candidato['similaridade'], reverse=True)
    return candidatos[:5]

def _texto_leilao(valor):
    if valor is None or pd.isna(valor):
        return ''
    return str(valor).strip()

def _normalizar_cabecalho_leilao(valor):
    texto = unicodedata.normalize('NFKD', str(valor))
    texto = ''.join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return re.sub(r'[^a-z0-9]+', ' ', texto.lower()).strip()

def _lote_leilao_valido(lotes, identificador):
    if identificador in (None, ''):
        return None
    if not isinstance(identificador, str) or not any(
        lote.get('id') == identificador for lote in lotes
    ):
        abort(400, description='Selecione um lote existente ou deixe o item sem lote.')
    return identificador

def _arquivo_leilao():
    return DADOS_TRABALHO_DIR / 'itens_leilao.json'

def _salvar_itens_leilao(itens):
    estado = _carregar_estado_leilao()
    estado['itens'] = itens
    _salvar_estado_leilao(estado)

def _normalizar_texto_correspondencia(valor):
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    texto = ''.join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return ' '.join(re.findall(r'[a-z0-9]+', texto.lower()))

def _token_analise_importacao_leilao(itens, lote_id, analise):
    conteudo = json.dumps(
        {
            'itens': [
                {chave: valor for chave, valor in item.items() if chave != 'id'}
                for item in itens
            ],
            'lote_id': lote_id,
            'analise': analise,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(conteudo.encode('utf-8')).hexdigest()

def _analisar_setores_importacao_leilao(itens, base):
    resumo = {
        'total': len(itens),
        'descricao_provisoria': sum(
            bool(item.get('descricao_provisoria')) for item in itens
        ),
        'propostas': 0,
        'sem_plaqueta': 0,
        'nao_encontrada': 0,
        'ambiguas': 0,
        'sem_setor': 0,
        'setor_igual': 0,
        'sem_base': 0,
        'descricoes_encontradas': 0,
    }
    propostas = []
    descricoes_resolvidas = []
    if base is not None and (
        'Plaqueta' not in base.columns or 'Setor Atual' not in base.columns
    ):
        raise ValueError('A base patrimonial precisa conter Plaqueta e Setor Atual.')

    por_plaqueta = {}
    if base is not None:
        for _, bem in base.iterrows():
            plaqueta = _plaqueta_normalizada(bem.get('Plaqueta'))
            if plaqueta:
                por_plaqueta.setdefault(plaqueta, []).append(bem)

    for indice, item in enumerate(itens):
        plaqueta = _plaqueta_normalizada(item.get('plaqueta_informada'))
        if not plaqueta:
            plaqueta = _plaqueta_normalizada(item.get('plaqueta_vinculada'))
        if not plaqueta:
            resumo['sem_plaqueta'] += 1
            continue
        if base is None:
            resumo['sem_base'] += 1
            continue
        correspondencias = por_plaqueta.get(plaqueta, [])
        if not correspondencias:
            resumo['nao_encontrada'] += 1
            continue
        if len(correspondencias) > 1:
            resumo['ambiguas'] += 1
            continue

        bem = correspondencias[0]
        descricao_bem = _texto_leilao(bem.get('Descrição', ''))
        if item.get('descricao_provisoria') and descricao_bem:
            descricoes_resolvidas.append({
                'indice': indice,
                'plaqueta': plaqueta,
                'descricao_cadastro': descricao_bem,
            })
        setor = _texto_leilao(bem.get('Setor Atual', ''))
        if not setor:
            resumo['sem_setor'] += 1
            continue
        setor_atual = _texto_leilao(item.get('setor', ''))
        if setor_atual == setor:
            resumo['setor_igual'] += 1
            continue
        propostas.append({
            'indice': indice,
            'descricao': item['descricao'],
            'plaqueta': plaqueta,
            'setor_atual': setor_atual,
            'setor_sugerido': setor,
            'descricao_cadastro': descricao_bem,
        })

    resumo['propostas'] = len(propostas)
    resumo['descricoes_encontradas'] = len(descricoes_resolvidas)
    return {
        'base_disponivel': base is not None,
        'resumo': resumo,
        'propostas': propostas,
        'descricoes_resolvidas': descricoes_resolvidas,
    }

def _lotes_com_numeracao(lotes, itens=None):
    itens = itens if itens is not None else []
    resultado = []
    for indice, lote in enumerate(lotes, start=1):
        valor_calculado = Decimal('0')
        for item in itens:
            if item.get('lote_id') != lote.get('id') or not item.get('valor'):
                continue
            try:
                valor_calculado += Decimal(str(item['valor']))
            except (InvalidOperation, ValueError):
                continue
        valor_manual = str(lote.get('valor') or '').strip()
        resultado.append({
            **lote,
            'numero': indice,
            'valor_calculado': f'{valor_calculado.quantize(Decimal("0.01")):.2f}',
            'valor': valor_manual,
        })
    return resultado

def _plaqueta_normalizada(valor):
    if valor is None or pd.isna(valor):
        return ''
    return formatar_plaqueta_exibicao(valor)

def _normalizar_nome_lote(nome):
    texto = unicodedata.normalize('NFKD', str(nome))
    texto = ''.join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return ' '.join(texto.split()).casefold()


def _normalizar_lote_importado(valor):
    nome = str(valor).strip()
    normalizado = _normalizar_nome_lote(nome)
    numero = re.fullmatch(r'(?:lote\s*)?0*(\d+)(?:\.0+)?', normalizado)
    if numero:
        numero_lote = int(numero.group(1))
        return f'numero:{numero_lote}', f'Lote {numero_lote:02d}'
    return f'nome:{normalizado}', nome


def _identificador_lote_importado(chave, ids_existentes):
    tentativa = 0
    while True:
        conteudo = f'lote-importado:{chave}:{tentativa}'
        identificador = hashlib.sha256(conteudo.encode('utf-8')).hexdigest()[:24]
        if identificador not in ids_existentes:
            return identificador
        tentativa += 1


def _planejar_lotes_importacao(itens, lotes, lote_id):
    lotes_por_chave = {}
    lotes_por_id = {lote['id']: lote for lote in lotes}
    for lote in lotes:
        chave, _ = _normalizar_lote_importado(lote.get('nome', ''))
        if chave in lotes_por_chave:
            if chave.startswith('numero:'):
                lotes_por_chave[chave] = None
            else:
                raise ValueError(
                    'Há mais de um lote cadastrado com o mesmo nome. '
                    'Revise os lotes antes de importar.'
                )
        else:
            lotes_por_chave[chave] = lote

    lote_formulario = lotes_por_id.get(lote_id)
    lotes_por_numero = {
        numero: lote for numero, lote in enumerate(lotes, start=1)
    }
    coluna_detectada = any(item.get('lote_importado') is not None for item in itens)
    ids_existentes = set(lotes_por_id)
    criados = []
    grupos = {}
    divergencias = {}
    itens_sem_lote = 0

    for item in itens:
        valor_importado = item.get('lote_importado')
        texto_importado = str(valor_importado or '').strip()
        lote = None
        if texto_importado:
            chave, nome = _normalizar_lote_importado(texto_importado)
            numero_importado = re.fullmatch(r'numero:(\d+)', chave)
            if numero_importado:
                lote = lotes_por_numero.get(int(numero_importado.group(1)))
            if lote is None and chave in lotes_por_chave:
                lote = lotes_por_chave[chave]
                if lote is None:
                    raise ValueError(
                        f'O valor "{texto_importado}" corresponde a mais de um lote. '
                        'Revise os lotes antes de importar.'
                    )
            if lote is None:
                lote = {
                    'id': _identificador_lote_importado(chave, ids_existentes),
                    'nome': nome,
                }
                lotes.append(lote)
                lotes_por_chave[chave] = lote
                lotes_por_id[lote['id']] = lote
                ids_existentes.add(lote['id'])
                criados.append(nome)
        lote_efetivo_id = lote['id'] if lote else lote_id
        item['lote_id'] = lote_efetivo_id
        lote_efetivo = lotes_por_id.get(lote_efetivo_id)
        nome_efetivo = (
            lote_efetivo['nome'] if lote_efetivo else 'Disponíveis, sem lote'
        )
        chave_grupo = lote_efetivo_id or ''
        grupo = grupos.setdefault(chave_grupo, {
            'lote_id': lote_efetivo_id,
            'nome': nome_efetivo,
            'quantidade': 0,
            'valores_planilha': [],
        })
        grupo['quantidade'] += 1
        if texto_importado and texto_importado not in grupo['valores_planilha']:
            grupo['valores_planilha'].append(texto_importado)
        if lote_efetivo_id is None:
            itens_sem_lote += 1

        if (
            lote is not None
            and lote_formulario is not None
            and lote_efetivo_id != lote_id
        ):
            divergencia = divergencias.setdefault(lote['id'], {
                'lote_planilha': lote['nome'],
                'lote_formulario': lote_formulario['nome'],
                'quantidade': 0,
                'valores_planilha': [],
            })
            divergencia['quantidade'] += 1
            if (
                texto_importado
                and texto_importado not in divergencia['valores_planilha']
            ):
                divergencia['valores_planilha'].append(texto_importado)

    return {
        'coluna_detectada': coluna_detectada,
        'lote_formulario': lote_formulario['nome'] if lote_formulario else '',
        'grupos': list(grupos.values()),
        'lotes_criados': criados,
        'divergencias': list(divergencias.values()),
        'itens_sem_lote': itens_sem_lote,
    }


def _carregar_itens_leilao():
    return _carregar_estado_leilao()['itens']

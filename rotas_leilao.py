from flask import Blueprint

import json
import pandas as pd
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from flask import (
    abort,
    jsonify,
    request,
    send_file,
)
from io import (
    BytesIO,
)
from preparar_dados import (
    formatar_plaqueta_exibicao,
)
from secrets import (
    token_hex,
)
from servicos_comuns import (
    _csrf_required,
    _normalizar_valor_leilao,
)
from servicos_leilao import (
    _analisar_setores_importacao_leilao,
    _carregar_estado_leilao,
    _carregar_itens_leilao,
    _ler_arquivo_leilao,
    _lote_leilao_valido,
    _lotes_com_numeracao,
    _normalizar_nome_lote,
    _planejar_lotes_importacao,
    _salvar_estado_leilao,
    _salvar_itens_leilao,
    STATUS_DIVERGENTE,
    _sugerir_correspondencias,
    _token_analise_importacao_leilao,
)
from servicos_patrimonio import (
    _carregar_base,
    _setores,
)
from utils import (
    escrever_aba_relatorio,
)

blueprint = Blueprint('leilao', __name__)


def _ajustar_valor_manual_lote(lotes, lote_id, delta):
    if not lote_id:
        return
    lote = next(
        (registro for registro in lotes if registro.get('id') == lote_id),
        None,
    )
    if lote is None or not lote.get('valor'):
        return
    try:
        atual = Decimal(str(lote['valor']))
        alteracao = Decimal(str(delta))
    except (InvalidOperation, ValueError):
        return
    novo = (atual + alteracao).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )
    lote['valor'] = f'{max(novo, Decimal("0")):.2f}'

@blueprint.get('/api/leilao')
def listar_itens_leilao():
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    return jsonify(
        total=len(estado['itens']),
        itens=estado['itens'],
        lotes=_lotes_com_numeracao(estado['lotes'], estado['itens']),
    )

@blueprint.get('/api/leilao/exportar')
def exportar_itens_leilao():
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))

    termo = _normalizar_nome_lote(request.args.get('q', ''))
    lote_id = request.args.get('lote_id', '')
    lotes = _lotes_com_numeracao(estado['lotes'], estado['itens'])
    lotes_por_id = {lote['id']: lote for lote in lotes}
    if lote_id not in ('', '__sem_lote__') and lote_id not in lotes_por_id:
        abort(400, description='O filtro selecionado não corresponde a um lote existente.')

    filtrados = []
    for indice, item in enumerate(estado['itens']):
        if lote_id == '__sem_lote__' and item.get('lote_id'):
            continue
        if lote_id and lote_id != '__sem_lote__' and item.get('lote_id') != lote_id:
            continue
        if termo:
            pesquisaveis = ' '.join(
                str(item.get(campo) or '')
                for campo in ('descricao', 'plaqueta_informada')
            )
            if termo not in _normalizar_nome_lote(pesquisaveis):
                continue
        filtrados.append((indice, item))

    ordem_lotes = {lote['id']: lote['numero'] for lote in lotes}
    filtrados.sort(key=lambda par: (
        ordem_lotes.get(par[1].get('lote_id'), len(lotes) + 1),
        par[0],
    ))

    def texto_planilha(valor):
        texto = '' if valor is None else str(valor)
        if texto.startswith(('=', '+', '-', '@')):
            return f"'{texto}"
        return texto

    linhas = []
    for _, item in filtrados:
        lote = lotes_por_id.get(item.get('lote_id'))
        nome_lote = (
            f"Lote {lote['numero']} · {lote['nome']}"
            if lote
            else 'Disponíveis, sem lote'
        )
        linhas.append({
            'Lote': nome_lote,
            'Valor do lote (R$)': (
                lote.get('valor') or None
                if lote
                else None
            ),
            'Plaqueta informada': item.get('plaqueta_informada', ''),
            'Plaqueta vinculada': item.get('plaqueta_vinculada', ''),
            'Descrição': item.get('descricao', ''),
            'Setor': item.get('setor', ''),
            'Data': item.get('data', ''),
            'Valor (R$)': item.get('valor') or None,
            'Observação': item.get('observacao', ''),
            'Situação': (
                'Identificado'
                if item.get('situacao') == 'IDENTIFICADO'
                else 'Pendente de identificação'
            ),
        })

    dados = pd.DataFrame(linhas, columns=[
        'Lote',
        'Valor do lote (R$)',
        'Plaqueta informada',
        'Plaqueta vinculada',
        'Descrição',
        'Setor',
        'Data',
        'Valor (R$)',
        'Observação',
        'Situação',
    ])
    for coluna in dados.columns.drop(['Valor (R$)', 'Valor do lote (R$)']):
        dados[coluna] = dados[coluna].map(texto_planilha)
    try:
        dados['Valor (R$)'] = pd.to_numeric(dados['Valor (R$)'], errors='raise')
        dados['Valor do lote (R$)'] = pd.to_numeric(
            dados['Valor do lote (R$)'], errors='raise'
        )
    except (TypeError, ValueError) as error:
        abort(409, description=f'Não foi possível exportar um valor de item inválido: {error}')

    arquivo = BytesIO()
    with pd.ExcelWriter(arquivo, engine='openpyxl') as escritor:
        escrever_aba_relatorio(escritor, dados, 'Itens do leilão')
    arquivo.seek(0)
    return send_file(
        arquivo,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='itens_leilao.xlsx',
    )

@blueprint.post('/api/leilao/lotes')
@_csrf_required
def criar_lote_leilao():
    dados = request.get_json(silent=True)
    nome = str(dados.get('nome', '') if isinstance(dados, dict) else '').strip()
    if not nome or len(nome) > 80:
        abort(400, description='Informe um nome de lote de até 80 caracteres.')
    try:
        valor = _normalizar_valor_leilao(
            dados.get('valor', '') if isinstance(dados, dict) else ''
        )
    except ValueError as error:
        abort(400, description=str(error))
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    nome_normalizado = _normalizar_nome_lote(nome)
    if any(
        _normalizar_nome_lote(lote.get('nome', '')) == nome_normalizado
        for lote in estado['lotes']
    ):
        abort(409, description='Já existe um lote com esse nome.')
    try:
        lote = {'id': token_hex(12), 'nome': nome, 'valor': valor}
        estado['lotes'].append(lote)
        _salvar_estado_leilao(estado)
    except OSError as error:
        abort(500, description=f'Não foi possível criar o lote: {error}')
    return jsonify(
        mensagem='Lote criado.',
        lote={**lote, 'numero': len(estado['lotes'])},
    )

@blueprint.post('/api/leilao/lotes/reordenar')
@_csrf_required
def reordenar_lotes_leilao():
    dados = request.get_json(silent=True)
    ids = dados.get('ids') if isinstance(dados, dict) else None
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        abort(400, description='Informe a ordem completa dos lotes.')
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    ids_existentes = [lote.get('id') for lote in estado['lotes']]
    if len(ids) != len(ids_existentes) or set(ids) != set(ids_existentes):
        abort(400, description='A ordem deve conter cada lote existente uma única vez.')
    lotes_por_id = {lote['id']: lote for lote in estado['lotes']}
    estado['lotes'] = [lotes_por_id[identificador] for identificador in ids]
    try:
        _salvar_estado_leilao(estado)
    except OSError as error:
        abort(500, description=f'Não foi possível reordenar os lotes: {error}')
    return jsonify(mensagem='Ordem dos lotes atualizada.', lotes=_lotes_com_numeracao(estado['lotes']))

@blueprint.post('/api/leilao/lotes/<identificador>/editar')
@_csrf_required
def editar_lote_leilao(identificador):
    dados = request.get_json(silent=True)
    nome = str(dados.get('nome', '') if isinstance(dados, dict) else '').strip()
    if not nome or len(nome) > 80:
        abort(400, description='Informe um nome de lote de até 80 caracteres.')
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    lote = next(
        (registro for registro in estado['lotes'] if registro.get('id') == identificador),
        None,
    )
    if lote is None:
        abort(404, description='Lote não encontrado.')
    try:
        valor = (
            _normalizar_valor_leilao(dados.get('valor'))
            if isinstance(dados, dict) and 'valor' in dados
            else lote.get('valor', '')
        )
    except ValueError as error:
        abort(400, description=str(error))
    nome_normalizado = _normalizar_nome_lote(nome)
    if any(
        registro.get('id') != identificador
        and _normalizar_nome_lote(registro.get('nome', '')) == nome_normalizado
        for registro in estado['lotes']
    ):
        abort(409, description='Já existe um lote com esse nome.')
    lote['nome'] = nome
    lote['valor'] = valor
    try:
        _salvar_estado_leilao(estado)
    except OSError as error:
        abort(500, description=f'Não foi possível renomear o lote: {error}')
    return jsonify(mensagem='Lote atualizado.', lote=lote)

@blueprint.post('/api/leilao/lotes/<identificador>/excluir')
@_csrf_required
def excluir_lote_leilao(identificador):
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    lote = next(
        (registro for registro in estado['lotes'] if registro.get('id') == identificador),
        None,
    )
    if lote is None:
        abort(404, description='Lote não encontrado.')
    if any(item.get('lote_id') == identificador for item in estado['itens']):
        abort(409, description='Remova os itens do lote antes de excluí-lo.')
    estado['lotes'].remove(lote)
    try:
        _salvar_estado_leilao(estado)
    except OSError as error:
        abort(500, description=f'Não foi possível excluir o lote: {error}')
    return jsonify(mensagem='Lote excluído.')

@blueprint.get('/api/leilao/<identificador>/sugestoes')
def sugerir_vinculos_leilao(identificador):
    try:
        itens = _carregar_itens_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    item = next((registro for registro in itens if registro.get('id') == identificador), None)
    if item is None:
        abort(404, description='Item da lista de leilão não encontrado.')
    try:
        base = _carregar_base()
    except ValueError as error:
        abort(409, description=str(error))
    if base is None:
        abort(409, description='Prepare a base patrimonial antes de buscar correspondências.')
    return jsonify(sugestoes=_sugerir_correspondencias(item, base))

def preparar_analise_importacao_leilao():
    arquivo = request.files.get('arquivo')
    if arquivo is None or not arquivo.filename:
        abort(400, description='Selecione um arquivo Excel ou CSV.')
    try:
        novos = _ler_arquivo_leilao(arquivo)
    except ValueError as error:
        abort(400, description=str(error))
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    lote_id = _lote_leilao_valido(
        estado['lotes'], request.form.get('lote_id')
    )
    try:
        lotes_importacao = _planejar_lotes_importacao(
            novos, estado['lotes'], lote_id
        )
        base = _carregar_base()
        analise = _analisar_setores_importacao_leilao(novos, base)
    except ValueError as error:
        abort(409, description=str(error))
    analise['lotes_importacao'] = lotes_importacao
    token = _token_analise_importacao_leilao(novos, lote_id, analise)
    return novos, estado, lote_id, analise, token

@blueprint.post('/api/leilao/importar/analisar')
@_csrf_required
def analisar_importacao_leilao():
    _, _, _, analise, token = preparar_analise_importacao_leilao()
    return jsonify(
        mensagem='Confira as sugestões de setor antes de aprovar a importação.',
        analise=analise,
        token_analise=token,
    )

@blueprint.post('/api/leilao/importar')
@_csrf_required
def importar_lista_leilao():
    novos, estado, lote_id, analise, token = preparar_analise_importacao_leilao()
    token_informado = request.form.get('token_analise', '')
    if not token_informado or token_informado != token:
        abort(
            409,
            description='A análise mudou ou expirou. Analise o arquivo novamente antes de importar.',
        )
    try:
        indices_aprovados = json.loads(request.form.get('indices_aprovados', ''))
    except (TypeError, json.JSONDecodeError):
        abort(400, description='Informe quais sugestões de setor foram aprovadas.')
    if (
        not isinstance(indices_aprovados, list)
        or any(type(indice) is not int for indice in indices_aprovados)
        or len(indices_aprovados) != len(set(indices_aprovados))
    ):
        abort(400, description='A seleção de sugestões aprovadas é inválida.')
    propostas_por_indice = {
        proposta['indice']: proposta for proposta in analise['propostas']
    }
    if any(indice not in propostas_por_indice for indice in indices_aprovados):
        abort(400, description='A seleção contém uma sugestão que não foi analisada.')
    descricoes_aplicadas = 0
    for resolvida in analise['descricoes_resolvidas']:
        item = novos[resolvida['indice']]
        if item.get('descricao_provisoria') and resolvida['descricao_cadastro']:
            item['descricao'] = resolvida['descricao_cadastro'][:500]
            descricoes_aplicadas += 1
    for indice in indices_aprovados:
        novos[indice]['setor'] = propostas_por_indice[indice]['setor_sugerido']
    for item in novos:
        item.pop('lote_importado', None)
        item.pop('descricao_provisoria', None)
    estado['itens'].extend(novos)
    try:
        _salvar_estado_leilao(estado)
    except OSError as error:
        abort(500, description=f'Não foi possível salvar a lista de leilão: {error}')
    lotes_criados = analise['lotes_importacao']['lotes_criados']
    mensagem_lotes = (
        f' Lote(s) criado(s): {", ".join(lotes_criados)}.'
        if lotes_criados
        else ''
    )
    mensagem_descricoes = (
        f'{descricoes_aplicadas} descrição(ões) preenchida(s) pela plaqueta; '
        if descricoes_aplicadas
        else ''
    )
    return jsonify(
        mensagem=(
            f'{len(novos)} item(ns) importado(s); '
            f'{mensagem_descricoes}'
            f'{len(indices_aprovados)} setor(es) aplicado(s) após aprovação.'
            f'{mensagem_lotes}'
        ),
        importados=len(novos),
        descricoes_aplicadas=descricoes_aplicadas,
        setores_aplicados=len(indices_aprovados),
        lotes_criados=lotes_criados,
        total=len(estado['itens']),
    )

@blueprint.post('/api/leilao/manual')
@_csrf_required
def incluir_item_leilao_manual():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        abort(400, description='Dados do item para leilão inválidos.')
    descricao = str(dados.get('descricao', '') or '').strip()
    if not descricao or len(descricao) > 500:
        abort(400, description='Informe uma descrição de até 500 caracteres.')
    valor_quantidade = dados.get('quantidade', 1)
    if isinstance(valor_quantidade, bool) or not isinstance(valor_quantidade, (int, str)):
        abort(400, description='A quantidade deve ser um número inteiro entre 1 e 999.')
    try:
        quantidade = int(valor_quantidade)
    except (TypeError, ValueError):
        abort(400, description='A quantidade deve ser um número inteiro entre 1 e 999.')
    if isinstance(dados.get('quantidade', 1), bool) or quantidade < 1 or quantidade > 999:
        abort(400, description='A quantidade deve ser um número inteiro entre 1 e 999.')
    observacao = str(dados.get('observacao', '') or '').strip()
    if len(observacao) > 2000:
        abort(400, description='A observação deve ter até 2000 caracteres.')
    item = {
        'plaqueta_informada': str(dados.get('plaqueta', '') or '').strip()[:100],
        'descricao': descricao,
        'setor': str(dados.get('setor', '') or '').strip()[:255],
        'data': str(dados.get('data', '') or '').strip()[:50],
        'valor': '',
        'observacao': observacao,
        'situacao': 'PENDENTE_IDENTIFICACAO',
        'plaqueta_vinculada': '',
    }
    try:
        item['valor'] = _normalizar_valor_leilao(dados.get('valor', ''))
    except ValueError as error:
        abort(400, description=str(error))
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    item['lote_id'] = _lote_leilao_valido(
        estado['lotes'], dados.get('lote_id')
    )
    novos = [{**item, 'id': token_hex(12)} for _ in range(quantidade)]
    estado['itens'].extend(novos)
    if item['lote_id'] and item['valor']:
        try:
            delta = Decimal(str(item['valor'])) * quantidade
        except (InvalidOperation, ValueError):
            delta = Decimal('0')
        _ajustar_valor_manual_lote(estado['lotes'], item['lote_id'], delta)
    try:
        _salvar_estado_leilao(estado)
    except OSError as error:
        abort(500, description=f'Não foi possível salvar o item: {error}')
    mensagem = (
        'Item adicionado como pendente de identificação.'
        if quantidade == 1
        else f'{quantidade} itens adicionados como pendentes de identificação.'
    )
    return jsonify(
        mensagem=mensagem,
        item=novos[0],
        itens=novos,
        quantidade=quantidade,
    )

@blueprint.post('/api/leilao/<identificador>/editar')
@_csrf_required
def editar_item_leilao(identificador):
    alteracoes = request.get_json(silent=True)
    if not isinstance(alteracoes, dict):
        abort(400, description='Dados de edição do item para leilão inválidos.')
    descricao = str(alteracoes.get('descricao', '') or '').strip()
    if not descricao or len(descricao) > 500:
        abort(400, description='Informe uma descrição de até 500 caracteres.')
    plaqueta = str(alteracoes.get('plaqueta', '') or '').strip()
    setor = str(alteracoes.get('setor', '') or '').strip()
    data_item = str(alteracoes.get('data', '') or '').strip()
    observacao = str(alteracoes.get('observacao', '') or '').strip()
    if len(plaqueta) > 100 or len(setor) > 255 or len(data_item) > 50:
        abort(400, description='Plaqueta, setor ou data excede o limite permitido.')
    if len(observacao) > 2000:
        abort(400, description='A observação deve ter até 2000 caracteres.')
    try:
        itens = _carregar_itens_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    item = next((registro for registro in itens if registro.get('id') == identificador), None)
    if item is None:
        abort(404, description='Item da lista de leilão não encontrado.')
    valor_anterior = item.get('valor', '')
    valor = item.get('valor', '')
    if 'valor' in alteracoes:
        try:
            valor = _normalizar_valor_leilao(alteracoes.get('valor'))
        except ValueError as error:
            abort(400, description=str(error))
    if setor != str(item.get('setor', '') or '').strip():
        try:
            base = _carregar_base()
        except ValueError as error:
            abort(409, description=str(error))
        if setor and (base is None or setor not in _setores(base)):
            abort(400, description='Selecione um setor existente.')
    item.update({
        'descricao': descricao,
        'plaqueta_informada': plaqueta,
        'setor': setor,
        'data': data_item,
        'valor': valor,
        'observacao': observacao,
    })
    try:
        _salvar_itens_leilao(itens)
    except OSError as error:
        abort(500, description=f'Não foi possível salvar as alterações: {error}')
    try:
        estado = _carregar_estado_leilao()
        try:
            anterior = Decimal(str(valor_anterior)) if valor_anterior else Decimal('0')
            novo = Decimal(str(valor)) if valor else Decimal('0')
        except (InvalidOperation, ValueError):
            anterior = novo = Decimal('0')
        _ajustar_valor_manual_lote(
            estado['lotes'],
            item.get('lote_id'),
            novo - anterior,
        )
        _salvar_estado_leilao(estado)
    except (OSError, ValueError) as error:
        abort(500, description=f'Não foi possível atualizar o valor do lote: {error}')
    return jsonify(mensagem='Item da lista de leilão atualizado.', item=item)

@blueprint.post('/api/leilao/<identificador>/excluir')
@_csrf_required
def excluir_item_leilao(identificador):
    try:
        itens = _carregar_itens_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    item = next((registro for registro in itens if registro.get('id') == identificador), None)
    if item is None:
        abort(404, description='Item da lista de leilão não encontrado.')
    try:
        estado = _carregar_estado_leilao()
        if item.get('valor'):
            _ajustar_valor_manual_lote(
                estado['lotes'],
                item.get('lote_id'),
                -Decimal(str(item['valor'])),
            )
    except (InvalidOperation, ValueError):
        estado = None
    itens.remove(item)
    try:
        _salvar_itens_leilao(itens)
    except OSError as error:
        abort(500, description=f'Não foi possível excluir o item: {error}')
    if estado is not None:
        try:
            estado['itens'] = itens
            _salvar_estado_leilao(estado)
        except OSError as error:
            abort(500, description=f'Não foi possível atualizar o valor do lote: {error}')
    return jsonify(mensagem='Item removido da lista de leilão.')

@blueprint.post('/api/leilao/excluir-selecionados')
@_csrf_required
def excluir_itens_leilao_selecionados():
    dados = request.get_json(silent=True)
    ids = dados.get('ids') if isinstance(dados, dict) else None
    if not isinstance(ids, list) or not ids:
        abort(400, description='Selecione ao menos um item para excluir.')
    if any(not isinstance(identificador, str) or not identificador for identificador in ids):
        abort(400, description='A seleção contém identificadores inválidos.')
    if len(ids) != len(set(ids)):
        abort(400, description='A seleção contém itens repetidos.')
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    ids_existentes = {item.get('id') for item in estado['itens']}
    if any(identificador not in ids_existentes for identificador in ids):
        abort(404, description='Um ou mais itens selecionados não foram encontrados.')
    ids_selecionados = set(ids)
    itens_selecionados = [
        item for item in estado['itens']
        if item.get('id') in ids_selecionados
    ]
    for item in itens_selecionados:
        if item.get('valor'):
            try:
                _ajustar_valor_manual_lote(
                    estado['lotes'],
                    item.get('lote_id'),
                    -Decimal(str(item['valor'])),
                )
            except (InvalidOperation, ValueError):
                pass
    estado['itens'] = [
        item for item in estado['itens']
        if item.get('id') not in ids_selecionados
    ]
    try:
        _salvar_estado_leilao(estado)
    except OSError as error:
        abort(500, description=f'Não foi possível excluir os itens: {error}')
    return jsonify(
        mensagem=f'{len(ids)} item(ns) excluído(s) da lista de leilão.',
        excluidos=len(ids),
        total=len(estado['itens']),
    )

@blueprint.post('/api/leilao/<identificador>/lote')
@_csrf_required
def mover_item_leilao(identificador):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        abort(400, description='Informe o lote de destino do item.')
    try:
        estado = _carregar_estado_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    item = next(
        (registro for registro in estado['itens'] if registro.get('id') == identificador),
        None,
    )
    if item is None:
        abort(404, description='Item da lista de leilão não encontrado.')
    lote_origem_id = item.get('lote_id')
    lote_destino_id = _lote_leilao_valido(estado['lotes'], dados.get('lote_id'))
    if lote_origem_id != lote_destino_id and item.get('valor'):
        try:
            valor_item = Decimal(str(item['valor']))
        except (InvalidOperation, ValueError):
            valor_item = Decimal('0')
        _ajustar_valor_manual_lote(
            estado['lotes'],
            lote_origem_id,
            -valor_item,
        )
        _ajustar_valor_manual_lote(
            estado['lotes'],
            lote_destino_id,
            valor_item,
        )
    item['lote_id'] = lote_destino_id
    try:
        _salvar_estado_leilao(estado)
    except OSError as error:
        abort(500, description=f'Não foi possível mover o item: {error}')
    mensagem = 'Item removido do lote e mantido na lista de disponíveis.'
    if item['lote_id']:
        mensagem = 'Item movido para o lote selecionado.'
    return jsonify(mensagem=mensagem, item=item)

@blueprint.post('/api/leilao/<identificador>/vincular')
@_csrf_required
def vincular_item_leilao(identificador):
    dados_requisicao = request.get_json(silent=True)
    if not isinstance(dados_requisicao, dict):
        abort(400, description='Dados do vínculo inválidos.')
    plaqueta = str(
        dados_requisicao.get('plaqueta', '')
    ).strip()
    identificador_patrimonial = dados_requisicao.get('identificador')
    tem_identificador = identificador_patrimonial not in (None, '')
    if not plaqueta and not tem_identificador:
        abort(400, description='Selecione um bem ou informe a plaqueta para vincular.')
    if plaqueta and tem_identificador:
        abort(400, description='Selecione um bem ou informe uma plaqueta, não ambos.')
    try:
        base = _carregar_base()
        itens = _carregar_itens_leilao()
    except ValueError as error:
        abort(409, description=str(error))
    if base is None:
        abort(409, description='Prepare a base patrimonial antes de vincular itens.')
    item = next((registro for registro in itens if registro.get('id') == identificador), None)
    if item is None:
        abort(404, description='Item da lista de leilão não encontrado.')

    mensagem_sem_baixa = 'Nenhuma baixa foi realizada.'
    if tem_identificador:
        if isinstance(identificador_patrimonial, bool):
            abort(400, description='O identificador do bem patrimonial é inválido.')
        try:
            indice = int(identificador_patrimonial)
        except (TypeError, ValueError):
            abort(400, description='O identificador do bem patrimonial é inválido.')
        if indice not in base.index:
            abort(404, description='O bem patrimonial selecionado não foi encontrado.')
        bem = base.loc[indice]
        if isinstance(bem, pd.DataFrame):
            abort(409, description='O identificador selecionado corresponde a mais de um bem.')
        plaqueta = formatar_plaqueta_exibicao(bem.get('Plaqueta', ''))
        status = str(bem.get('STATUS', '')).strip().upper()
        descricao = str(bem.get('Descrição', '') or '')
        setor = str(bem.get('Setor Atual', '') or '')
        item.update({
            'identificador_vinculado': indice,
            'descricao_vinculada': descricao,
            'setor_vinculado': setor,
            'status_vinculado': status,
        })
        mensagem = (
            f'Item vinculado à plaqueta {plaqueta}. {mensagem_sem_baixa}'
            if plaqueta
            else f'Item vinculado ao bem sem plaqueta "{descricao}". {mensagem_sem_baixa}'
        )
        if status in STATUS_DIVERGENTE:
            mensagem += (
                f' Atenção: o bem está marcado como {status}; confira se o setor está '
                'correto, pois pode estar cadastrado em outro setor.'
            )
    else:
        plaquetas_base = {
            formatar_plaqueta_exibicao(valor)
            for valor in base.get('Plaqueta', pd.Series(dtype=object))
        }
        if plaqueta not in plaquetas_base:
            abort(404, description='A plaqueta informada não foi encontrada na base patrimonial.')
        for campo in (
            'identificador_vinculado',
            'descricao_vinculada',
            'setor_vinculado',
            'status_vinculado',
        ):
            item.pop(campo, None)
        mensagem = f'Item vinculado à plaqueta {plaqueta}. {mensagem_sem_baixa}'

    item['plaqueta_vinculada'] = plaqueta
    item['situacao'] = 'IDENTIFICADO'
    try:
        _salvar_itens_leilao(itens)
    except OSError as error:
        abort(500, description=f'Não foi possível salvar o vínculo: {error}')
    return jsonify(
        mensagem=mensagem,
        item=item,
    )

from flask import Blueprint

import pandas as pd
from constantes import (
    ESTADO_OPCOES,
    STATUS_OPCOES,
)
from flask import (
    abort,
    g,
    jsonify,
    request,
)
from dados_patrimoniais import enriquecer_com_dados_patrimoniais
from autenticacao import (
    requer_administrador,
    requer_permissao,
)
from pathlib import (
    Path,
)
from paths import (
    ARQUIVO_CAMPOS,
    ARQUIVO_RESULTADO,
    DATA_DIR,
    FOTOS_DIR,
)
from preparar_dados import (
    criar_registros_sem_plaqueta,
)
from servicos_comuns import (
    _csrf_required,
    _pagina_requisitada,
    _registros_json,
)
from servicos_operacoes import (
    _salvar_planilha,
)
from servicos_patrimonio import (
    _aplicar_alteracoes_item,
    _aplicar_filtros_busca,
    _carregar_base,
    _preparar_imagem,
    _setores,
)

blueprint = Blueprint('patrimonio', __name__)

@blueprint.get('/api/setores')
def setores():
    try:
        dados = _carregar_base()
    except ValueError as error:
        abort(409, description=str(error))
    if dados is None:
        return jsonify(setores=[])
    return jsonify(setores=_setores(dados))

@blueprint.get('/api/conferencia')
def conferencia():
    setor = request.args.get('setor', '').strip()
    if not setor:
        abort(400, description='Selecione um setor para iniciar a conferência.')
    try:
        dados = _carregar_base()
    except ValueError as error:
        abort(409, description=str(error))
    if dados is None:
        abort(409, description='Ainda não há uma base preparada.')
    if setor not in _setores(dados):
        abort(404, description='O setor selecionado não foi encontrado na base.')
    dados = dados[dados['Setor Atual'].astype(str).str.strip() == setor].copy()
    total = len(dados)
    pagina, por_pagina, inicio, fim = _pagina_requisitada(total)
    itens = enriquecer_com_dados_patrimoniais(dados.iloc[inicio:fim])
    return jsonify(
        setor=setor,
        total=total,
        pagina=pagina,
        por_pagina=por_pagina,
        itens=_registros_json(itens),
    )

@blueprint.get('/api/busca')
def busca():
    try:
        dados = _carregar_base()
    except ValueError as error:
        abort(409, description=str(error))
    if dados is None:
        return jsonify(total=0, itens=[])
    try:
        filtrados = _aplicar_filtros_busca(
            dados,
            {
                'termo': request.args.get('termo', ''),
                'setores': request.args.getlist('setor'),
                'status': request.args.getlist('status'),
                'foto': request.args.getlist('foto'),
                'plaqueta': request.args.get('plaqueta', ''),
                'data_inicial': request.args.get('data_inicial', ''),
                'data_final': request.args.get('data_final', ''),
            },
        )
    except ValueError:
        abort(400, description='Informe as datas usando um período válido.')
    total = len(filtrados)
    pagina, por_pagina, inicio, fim = _pagina_requisitada(total)
    itens = enriquecer_com_dados_patrimoniais(filtrados.iloc[inicio:fim])
    return jsonify(
        total=total,
        pagina=pagina,
        por_pagina=por_pagina,
        itens=_registros_json(itens),
    )


@blueprint.post('/api/conferencia/incluir')
@requer_administrador
@_csrf_required
def incluir_item():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        abort(400, description='Dados do item inválidos.')
    try:
        base = _carregar_base()
    except ValueError as error:
        abort(409, description=str(error))
    if base is None:
        abort(409, description='Prepare a base antes de incluir um bem.')
    setor = str(dados.get('setor', '')).strip()
    descricao = str(dados.get('descricao', '')).strip()
    if setor not in _setores(base):
        abort(400, description='Selecione um setor existente.')
    if not descricao or len(descricao) > 500:
        abort(400, description='Informe uma descrição de até 500 caracteres.')
    try:
        quantidade = int(dados.get('quantidade', 1))
    except (TypeError, ValueError):
        abort(400, description='Quantidade inválida.')
    if quantidade < 1 or quantidade > 999:
        abort(400, description='A quantidade deve estar entre 1 e 999.')
    status = str(dados.get('status', 'DIVERG')).strip().upper()
    estado = str(dados.get('estado', 'EM USO')).strip().upper()
    if status not in STATUS_OPCOES or estado not in ESTADO_OPCOES:
        abort(400, description='Status ou estado de conservação inválido.')
    if status in ('N_LOC', 'BAIXA'):
        estado = 'NAO_APLICAVEL'
    elif estado == 'NAO_APLICAVEL':
        estado = 'EM USO'
    observacao = str(dados.get('observacao', '')).strip()
    if len(observacao) > 2000:
        abort(400, description='A observação excede 2.000 caracteres.')
    novos = criar_registros_sem_plaqueta(
        quantidade,
        descricao,
        setor,
        status,
        estado,
        observacao or 'ITEM LOCALIZADO SEM PLAQUETA - PENDENTE DE CADASTRO',
    )
    for coluna in base.columns:
        if coluna not in novos.columns:
            novos[coluna] = ''
    for coluna in novos.columns:
        if coluna not in base.columns:
            base[coluna] = ''
    base = pd.concat([base, novos[base.columns]], ignore_index=True)
    _salvar_planilha(base)
    return jsonify(mensagem=f'{quantidade} bem(ns) incluído(s).', total=len(base))

@blueprint.post('/api/itens/<int:indice>/editar')
@requer_permissao('conferencia:registrar')
@_csrf_required
def editar_item(indice):
    alteracoes = request.get_json(silent=True)
    if not isinstance(alteracoes, dict):
        abort(400, description='Dados de edição inválidos.')
    if g.usuario_atual['funcao'] != 'admin':
        campos_permitidos = {'status', 'estado', 'observacao'}
        if set(alteracoes).difference(campos_permitidos):
            abort(403, description='Você só pode registrar dados da conferência.')
    try:
        dados = _carregar_base()
    except ValueError as error:
        abort(409, description=str(error))
    if dados is None or indice not in dados.index:
        abort(404, description='Item não encontrado.')
    dados = _aplicar_alteracoes_item(dados, indice, alteracoes)
    _salvar_planilha(dados)
    return jsonify(mensagem='Conferência salva.')

@blueprint.post('/api/itens/excluir-lote')
@requer_administrador
@_csrf_required
def excluir_itens_em_lote():
    payload = request.get_json(silent=True)
    ids = payload.get('ids') if isinstance(payload, dict) else None
    if not isinstance(ids, list) or not ids:
        abort(400, description='Selecione ao menos um item para excluir.')
    try:
        indices = [int(indice) for indice in ids]
    except (TypeError, ValueError):
        abort(400, description='A seleção contém identificadores inválidos.')
    if len(indices) != len(set(indices)):
        abort(400, description='A seleção contém itens repetidos.')
    try:
        dados = _carregar_base()
    except ValueError as error:
        abort(409, description=str(error))
    if dados is None or any(indice not in dados.index for indice in indices):
        abort(404, description='Um ou mais itens selecionados não foram encontrados.')
    dados = dados.drop(index=indices).reset_index(drop=True)
    try:
        _salvar_planilha(dados)
    except (OSError, ValueError) as error:
        abort(500, description=f'Não foi possível excluir os itens: {error}')
    return jsonify(mensagem=f'{len(indices)} item(ns) excluído(s) da base patrimonial.')

@blueprint.post('/api/itens/<int:indice>/foto')
@requer_permissao('conferencia:registrar')
@_csrf_required
def atualizar_foto(indice):
    if g.usuario_atual['funcao'] != 'admin':
        campos_permitidos = {'status', 'estado', 'observacao'}
        if (
            set(request.form).difference(campos_permitidos)
            or request.form.get('remover') == 'sim'
        ):
            abort(403, description='Você pode registrar a conferência, mas não remover fotos.')
    try:
        dados = _carregar_base()
    except ValueError as error:
        abort(409, description=str(error))
    if dados is None or indice not in dados.index:
        abort(404, description='Item não encontrado.')
    if 'Foto' not in dados.columns:
        dados['Foto'] = ''
    foto_atual = str(dados.at[indice, 'Foto']).strip()
    foto_nova = ''
    caminho_novo = None
    alteracoes = {
        coluna: request.form[coluna]
        for coluna in ('status', 'estado', 'observacao', 'descricao', 'valor')
        if coluna in request.form
    }
    if alteracoes:
        dados = _aplicar_alteracoes_item(dados, indice, alteracoes)

    if request.form.get('remover') == 'sim':
        dados.at[indice, 'Foto'] = ''
    else:
        upload = request.files.get('arquivo')
        if upload is None or not upload.filename:
            abort(400, description='Selecione uma imagem ou confirme a remoção.')
        foto_nova = _preparar_imagem(upload)
        caminho_novo = FOTOS_DIR / foto_nova
        dados.at[indice, 'Foto'] = foto_nova

    salvou = False
    try:
        _salvar_planilha(dados)
        salvou = True
    finally:
        if not salvou and caminho_novo is not None:
            caminho_novo.unlink(missing_ok=True)

    if foto_atual and foto_atual != foto_nova:
        antigo = FOTOS_DIR / Path(foto_atual).name
        if antigo.parent == FOTOS_DIR:
            antigo.unlink(missing_ok=True)
    return jsonify(
        mensagem='Foto e conferência atualizadas.',
        foto=foto_nova,
        pasta=str(FOTOS_DIR.relative_to(DATA_DIR)),
        planilhas=[
            Path(ARQUIVO_RESULTADO).name,
            Path(ARQUIVO_CAMPOS).name,
        ],
    )

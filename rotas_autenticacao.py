from flask import (
    Blueprint,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from secrets import token_urlsafe

from autenticacao import (
    autenticar_usuario,
    alterar_estado_operador,
    criar_usuario,
    listar_operadores,
    redefinir_senha_operador,
    requer_administrador,
    trocar_senha_usuario,
    validar_senha,
    validar_senha_atual,
)
from servicos_comuns import _csrf_required, _validar_csrf

blueprint = Blueprint('autenticacao', __name__)


def _token_csrf():
    return session.setdefault('csrf_token', token_urlsafe(32))


@blueprint.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html', csrf_token=_token_csrf())

    _validar_csrf()
    token = session['csrf_token']

    usuario = autenticar_usuario(
        request.form.get('nome_usuario', ''),
        request.form.get('senha', ''),
        request.remote_addr,
    )
    if usuario is None:
        return render_template(
            'login.html',
            csrf_token=token,
            erro='Usuário ou senha inválidos. Aguarde alguns minutos se houver muitas tentativas.',
        ), 401

    session.clear()
    session.permanent = True
    session['usuario_id'] = usuario['id']
    _token_csrf()
    destino = 'autenticacao.trocar_senha' if usuario['trocar_senha'] else 'operacoes.index'
    return redirect(url_for(destino))


@blueprint.post('/logout')
@_csrf_required
def logout():
    session.clear()
    return redirect(url_for('autenticacao.login'))


@blueprint.route('/trocar-senha', methods=['GET', 'POST'])
def trocar_senha():
    if request.method == 'GET':
        return render_template(
            'trocar_senha.html',
            csrf_token=_token_csrf(),
            obrigatoria=g.usuario_atual['trocar_senha'],
        )
    _validar_csrf()
    usuario = g.usuario_atual
    senha_atual = request.form.get('senha_atual', '')
    senha_nova = request.form.get('senha_nova', '')
    if not validar_senha_atual(usuario['id'], senha_atual):
        return render_template(
            'trocar_senha.html',
            csrf_token=_token_csrf(),
            obrigatoria=usuario['trocar_senha'],
            erro='A senha atual está incorreta.',
        ), 400
    try:
        validar_senha(senha_nova)
    except ValueError as error:
        return render_template(
            'trocar_senha.html',
            csrf_token=_token_csrf(),
            obrigatoria=usuario['trocar_senha'],
            erro=str(error),
        ), 400
    if senha_nova != request.form.get('confirmar_senha', ''):
        return render_template(
            'trocar_senha.html',
            csrf_token=_token_csrf(),
            obrigatoria=usuario['trocar_senha'],
            erro='A confirmação da nova senha não confere.',
        ), 400
    if not trocar_senha_usuario(usuario['id'], senha_nova):
        abort(409, description='A conta não está mais ativa. Entre em contato com o administrador.')

    session.clear()
    session.permanent = True
    session['usuario_id'] = usuario['id']
    _token_csrf()
    return redirect(url_for('operacoes.index'))


@blueprint.get('/api/admin/usuarios')
@requer_administrador
def usuarios():
    return jsonify(usuarios=listar_operadores())


@blueprint.post('/api/admin/usuarios')
@requer_administrador
@_csrf_required
def criar_operador():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        abort(400, description='Dados do usuário inválidos.')
    try:
        usuario = criar_usuario(
            dados.get('nome_usuario'),
            dados.get('senha_temporaria'),
            funcao='operador',
            trocar_senha=True,
        )
    except ValueError as error:
        abort(400, description=str(error))
    return jsonify(mensagem='Conta criada. O usuário deverá trocar a senha no primeiro acesso.', usuario=usuario), 201


@blueprint.post('/api/admin/usuarios/<int:usuario_id>/senha')
@requer_administrador
@_csrf_required
def redefinir_senha(usuario_id):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        abort(400, description='Dados de redefinição inválidos.')
    try:
        usuario = redefinir_senha_operador(
            usuario_id,
            dados.get('senha_temporaria'),
        )
    except ValueError as error:
        abort(400, description=str(error))
    if usuario is None:
        abort(404, description='Conta de usuário não encontrada.')
    return jsonify(mensagem='Senha redefinida. Será solicitada a troca no próximo acesso.')


@blueprint.post('/api/admin/usuarios/<int:usuario_id>/estado')
@requer_administrador
@_csrf_required
def alterar_estado_usuario(usuario_id):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict) or type(dados.get('ativo')) is not bool:
        abort(400, description='Informe se a conta deve ficar ativa.')
    usuario = alterar_estado_operador(usuario_id, dados['ativo'])
    if usuario is None:
        abort(404, description='Conta de usuário não encontrada.')
    return jsonify(mensagem='Estado da conta atualizado.', usuario=usuario)

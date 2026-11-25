import getpass
import re
import sqlite3
import time
from contextlib import closing
from functools import wraps

from flask import abort, g
from werkzeug.security import check_password_hash, generate_password_hash

from paths import DATA_DIR

AUTH_DATABASE = DATA_DIR / 'usuarios.sqlite3'
MIN_PASSWORD_LENGTH = 12
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCK_SECONDS = 900
_HASH_SENHA_INEXISTENTE = generate_password_hash('senha-dummy-para-tempo-constante')


def _conectar():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(AUTH_DATABASE, timeout=10)
    conexao.row_factory = sqlite3.Row
    return conexao


def inicializar_armazenamento_autenticacao():
    with closing(_conectar()) as conexao:
        conexao.executescript(
            '''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY,
                nome_usuario TEXT NOT NULL COLLATE NOCASE UNIQUE,
                senha_hash TEXT NOT NULL,
                funcao TEXT NOT NULL CHECK (funcao IN ('admin', 'operador')),
                ativo INTEGER NOT NULL DEFAULT 1,
                trocar_senha INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tentativas_login (
                nome_usuario TEXT NOT NULL COLLATE NOCASE,
                endereco TEXT NOT NULL,
                tentativas INTEGER NOT NULL,
                bloqueado_ate INTEGER NOT NULL,
                atualizado_em INTEGER NOT NULL,
                PRIMARY KEY (nome_usuario, endereco)
            );
            '''
        )
        conexao.commit()


def _nome_usuario_valido(nome_usuario):
    nome = str(nome_usuario or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9._-]{3,50}', nome):
        raise ValueError(
            'O usuário deve ter de 3 a 50 caracteres: letras, números, ponto, hífen ou sublinhado.'
        )
    return nome


def validar_senha(senha):
    if not isinstance(senha, str) or len(senha) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f'A senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.'
        )
    if len(senha) > 256:
        raise ValueError('A senha excede o limite permitido.')
    return senha


def _usuario_publico(linha):
    return {
        'id': int(linha['id']),
        'nome_usuario': linha['nome_usuario'],
        'funcao': linha['funcao'],
        'ativo': bool(linha['ativo']),
        'trocar_senha': bool(linha['trocar_senha']),
        'criado_em': linha['criado_em'],
    }


def obter_usuario(usuario_id):
    if not usuario_id:
        return None
    with closing(_conectar()) as conexao:
        linha = conexao.execute(
            '''
            SELECT id, nome_usuario, funcao, ativo, trocar_senha, criado_em
            FROM usuarios WHERE id = ?
            ''',
            (usuario_id,),
        ).fetchone()
    return _usuario_publico(linha) if linha else None


def criar_usuario(nome_usuario, senha, funcao='operador', trocar_senha=False):
    nome = _nome_usuario_valido(nome_usuario)
    validar_senha(senha)
    if funcao not in {'admin', 'operador'}:
        raise ValueError('Tipo de usuário inválido.')
    criado_em = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        with closing(_conectar()) as conexao:
            cursor = conexao.execute(
                '''
                INSERT INTO usuarios
                    (nome_usuario, senha_hash, funcao, trocar_senha, criado_em)
                VALUES (?, ?, ?, ?, ?)
                ''',
                (
                    nome,
                    generate_password_hash(senha),
                    funcao,
                    int(trocar_senha),
                    criado_em,
                ),
            )
            conexao.commit()
            usuario_id = cursor.lastrowid
    except sqlite3.IntegrityError as error:
        raise ValueError('Já existe uma conta com esse nome de usuário.') from error
    return obter_usuario(usuario_id)


def autenticar_usuario(nome_usuario, senha, endereco):
    nome = str(nome_usuario or '').strip()[:50]
    endereco = str(endereco or 'desconhecido')[:100]
    agora = int(time.time())
    with closing(_conectar()) as conexao:
        conexao.execute(
            'DELETE FROM tentativas_login WHERE atualizado_em < ?',
            (agora - 86400,),
        )
        tentativa = conexao.execute(
            '''
            SELECT tentativas, bloqueado_ate FROM tentativas_login
            WHERE nome_usuario = ? AND endereco = ?
            ''',
            (nome, endereco),
        ).fetchone()
        if tentativa and tentativa['bloqueado_ate'] > agora:
            return None

        linha = conexao.execute(
            '''
            SELECT id, nome_usuario, senha_hash, funcao, ativo, trocar_senha, criado_em
            FROM usuarios WHERE nome_usuario = ? COLLATE NOCASE
            ''',
            (nome,),
        ).fetchone()
        senha_hash = (
            linha['senha_hash'] if linha else _HASH_SENHA_INEXISTENTE
        )
        senha_correta = check_password_hash(senha_hash, senha)
        if not senha_correta or not linha['ativo']:
            tentativas = (tentativa['tentativas'] if tentativa else 0) + 1
            bloqueado_ate = (
                agora + LOGIN_LOCK_SECONDS
                if tentativas >= MAX_LOGIN_ATTEMPTS
                else 0
            )
            conexao.execute(
                '''
                INSERT INTO tentativas_login
                    (nome_usuario, endereco, tentativas, bloqueado_ate, atualizado_em)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(nome_usuario, endereco) DO UPDATE SET
                    tentativas = excluded.tentativas,
                    bloqueado_ate = excluded.bloqueado_ate,
                    atualizado_em = excluded.atualizado_em
                ''',
                (nome, endereco, tentativas, bloqueado_ate, agora),
            )
            conexao.commit()
            return None

        conexao.execute(
            'DELETE FROM tentativas_login WHERE nome_usuario = ? AND endereco = ?',
            (nome, endereco),
        )
        conexao.commit()
        return _usuario_publico(linha)


def listar_operadores():
    with closing(_conectar()) as conexao:
        linhas = conexao.execute(
            '''
            SELECT id, nome_usuario, funcao, ativo, trocar_senha, criado_em
            FROM usuarios WHERE funcao = 'operador'
            ORDER BY nome_usuario COLLATE NOCASE
            '''
        ).fetchall()
    return [_usuario_publico(linha) for linha in linhas]


def alterar_estado_operador(usuario_id, ativo):
    with closing(_conectar()) as conexao:
        cursor = conexao.execute(
            '''
            UPDATE usuarios SET ativo = ?
            WHERE id = ? AND funcao = 'operador'
            ''',
            (int(ativo), usuario_id),
        )
        conexao.commit()
        if cursor.rowcount != 1:
            return None
    return obter_usuario(usuario_id)


def redefinir_senha_operador(usuario_id, senha):
    validar_senha(senha)
    with closing(_conectar()) as conexao:
        cursor = conexao.execute(
            '''
            UPDATE usuarios
            SET senha_hash = ?, trocar_senha = 1
            WHERE id = ? AND funcao = 'operador'
            ''',
            (generate_password_hash(senha), usuario_id),
        )
        conexao.commit()
        if cursor.rowcount != 1:
            return None
    return obter_usuario(usuario_id)


def trocar_senha_usuario(usuario_id, senha):
    validar_senha(senha)
    with closing(_conectar()) as conexao:
        cursor = conexao.execute(
            '''
            UPDATE usuarios SET senha_hash = ?, trocar_senha = 0
            WHERE id = ? AND ativo = 1
            ''',
            (generate_password_hash(senha), usuario_id),
        )
        conexao.commit()
        return cursor.rowcount == 1


def validar_senha_atual(usuario_id, senha):
    with closing(_conectar()) as conexao:
        linha = conexao.execute(
            'SELECT senha_hash FROM usuarios WHERE id = ? AND ativo = 1',
            (usuario_id,),
        ).fetchone()
    return bool(linha and check_password_hash(linha['senha_hash'], senha))


def criar_administrador_inicial():
    inicializar_armazenamento_autenticacao()
    with closing(_conectar()) as conexao:
        existente = conexao.execute(
            "SELECT 1 FROM usuarios WHERE funcao = 'admin' LIMIT 1"
        ).fetchone()
    if existente:
        raise ValueError('Já existe uma conta administradora.')

    nome = input('Nome de usuário do administrador: ').strip()
    senha = getpass.getpass('Senha (mínimo 12 caracteres): ')
    confirmacao = getpass.getpass('Confirme a senha: ')
    if senha != confirmacao:
        raise ValueError('As senhas não conferem.')
    return criar_usuario(nome, senha, funcao='admin')


def requer_administrador(funcao):
    @wraps(funcao)
    def verificar(*args, **kwargs):
        usuario = getattr(g, 'usuario_atual', None)
        if not usuario or usuario['funcao'] != 'admin':
            abort(403, description='Somente o administrador pode realizar esta operação.')
        return funcao(*args, **kwargs)
    return verificar


def requer_permissao(permissao):
    def decorar(funcao):
        @wraps(funcao)
        def verificar(*args, **kwargs):
            usuario = getattr(g, 'usuario_atual', None)
            if not usuario:
                abort(401, description='Faça login para continuar.')
            permissoes = (
                {'conferencia:registrar'}
                if usuario['funcao'] == 'operador'
                else set()
            )
            if usuario['funcao'] != 'admin' and permissao not in permissoes:
                abort(403, description='Você não tem permissão para esta operação.')
            return funcao(*args, **kwargs)
        return verificar
    return decorar

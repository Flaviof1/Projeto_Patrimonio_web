import pandas as pd
import tempfile
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_HALF_UP,
)
from flask import (
    abort,
    request,
    session,
)
from functools import (
    wraps,
)
from pathlib import (
    Path,
)
from paths import (
    DATA_DIR,
)
from werkzeug.utils import (
    secure_filename,
)
def _normalizar_valor(valor):
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(valor, 'item'):
        valor = valor.item()
    if isinstance(valor, (str, int, float, bool)):
        return valor
    return str(valor)

def _normalizar_valor_leilao(valor):
    if valor is None:
        return ''
    if isinstance(valor, bool) or isinstance(valor, (list, dict)):
        raise ValueError('Informe um valor válido, como 1234,56.')
    if pd.isna(valor) or not str(valor).strip():
        return ''
    texto = str(valor).strip().replace('R$', '').replace('\u00a0', '').replace(' ', '')
    if ',' in texto and '.' in texto:
        if texto.rfind(',') > texto.rfind('.'):
            texto = texto.replace('.', '').replace(',', '.')
        else:
            texto = texto.replace(',', '')
    elif ',' in texto:
        partes = texto.split(',')
        if len(partes) == 2 and len(partes[1]) in (1, 2):
            texto = '.'.join(partes)
        elif len(partes) == 2 and len(partes[1]) == 3:
            texto = ''.join(partes)
        else:
            raise ValueError('Informe um valor válido, como 1234,56.')
    elif '.' in texto:
        partes = texto.split('.')
        if len(partes) > 2 and all(len(parte) == 3 for parte in partes[1:]):
            texto = ''.join(partes)
        elif len(partes) == 2 and len(partes[1]) == 3:
            texto = ''.join(partes)
    try:
        quantia = Decimal(texto).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError('Informe um valor válido, como 1234,56.') from None
    if not quantia.is_finite() or quantia < 0:
        raise ValueError('O valor deve ser zero ou maior.')
    if quantia > Decimal('999999999999.99'):
        raise ValueError('O valor excede o limite permitido.')
    return f'{quantia:.2f}'

def _temp_upload(upload, extensao_esperada):
    if upload is None or not upload.filename:
        abort(400, description='Selecione um arquivo para importar.')
    nome_seguro = secure_filename(upload.filename)
    if Path(nome_seguro).suffix.lower() != extensao_esperada:
        abort(400, description=f'O arquivo deve ter a extensão {extensao_esperada}.')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporario = tempfile.NamedTemporaryFile(
        prefix='importacao_', suffix=extensao_esperada, dir=DATA_DIR, delete=False
    )
    caminho = Path(temporario.name)
    temporario.close()
    try:
        upload.save(caminho)
    except OSError:
        caminho.unlink(missing_ok=True)
        raise
    return caminho

def _csrf_required(funcao):
    @wraps(funcao)
    def validar_csrf(*args, **kwargs):
        token = session.get('csrf_token')
        enviado = request.headers.get('X-CSRF-Token')
        if not token or not enviado or token != enviado:
            abort(400, description='Sessão inválida. Atualize a página e tente novamente.')
        return funcao(*args, **kwargs)
    return validar_csrf

def _confirmar_substituicao(caminho):
    if caminho.exists() and request.form.get('confirmar_substituicao') != 'sim':
        abort(
            409,
            description='Este arquivo já existe. Confirme a substituição antes de importar.',
        )

def _pagina_requisitada(total):
    try:
        pagina = int(request.args.get('pagina', '1'))
        por_pagina = int(request.args.get('por_pagina', '50'))
    except ValueError:
        abort(400, description='A página e a quantidade de itens devem ser números inteiros.')
    if pagina < 1 or por_pagina < 1 or por_pagina > 100:
        abort(400, description='Página inválida. São permitidos de 1 a 100 itens por página.')
    pagina = min(pagina, max(1, (total + por_pagina - 1) // por_pagina))
    inicio = (pagina - 1) * por_pagina
    return pagina, por_pagina, inicio, min(inicio + por_pagina, total)

def _registros_json(df):
    registros = []
    for indice, linha in df.iterrows():
        registro = {'id': int(indice)}
        registro.update({
            str(coluna): _normalizar_valor(valor)
            for coluna, valor in linha.items()
        })
        registros.append(registro)
    return registros

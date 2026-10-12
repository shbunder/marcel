import uvicorn

from .app import create_app
from .settings import port

uvicorn.run(create_app(), host='127.0.0.1', port=port(), log_level='info')

"""The real server can upgrade to WebSocket. TestClient speaks WebSocket without a library, so only
this test notices when uvicorn has none: then `/channel` and `/api/ws` answer 404 in production."""

from uvicorn.config import Config

from marcel_hub.app import Settings, create_app


def test_uvicorn_has_a_websocket_implementation(tmp_path) -> None:
    config = Config(create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'm.db')), ws='auto')
    config.load()
    assert config.ws_protocol_class is not None

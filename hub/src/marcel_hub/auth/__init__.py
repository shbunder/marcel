"""Device pairing and the device-token check (B-27).

`install(app)` adds `POST /pair` (public), the token-protected `/me` and `/devices…` routes, and
the handlers that turn every error into the contract's `Error` shape. Other routers protect
themselves with `dependencies=[Depends(require_device)]` and read the caller with
`current_device(request)`.
"""

from fastapi import FastAPI

from marcel_hub.auth.deps import current_device, require_device
from marcel_hub.auth.errors import ApiError, install_error_handlers
from marcel_hub.auth.routes import pair_router, router
from marcel_hub.auth.tokens import PairGuessLimit

__all__ = ['ApiError', 'current_device', 'install', 'require_device']


def install(app: FastAPI) -> None:
    install_error_handlers(app)
    app.state.pair_guess_limit = PairGuessLimit()
    app.include_router(pair_router)
    app.include_router(router)

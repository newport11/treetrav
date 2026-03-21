from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.serving import run_simple

from app import create_app
from shardops import create_app as create_shardops

treetrav_app = create_app()
shardops_app = create_shardops()

application = DispatcherMiddleware(treetrav_app, {
    "/shardops": shardops_app,
})

if __name__ == "__main__":
    run_simple("127.0.0.1", 5000, application, use_reloader=True, use_debugger=True)

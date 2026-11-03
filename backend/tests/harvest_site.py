"""A tiny local site for harvest tests: login, inventory (behind a form post), cart."""

import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGES = {
    "login.html": """<html><body><h1>Shop</h1>
<form action="/inventory.html" method="get">
  <label for="user">Username</label><input id="user" name="user-name">
  <label for="pass">Password</label><input id="pass" type="password" name="password">
  <button id="login" type="submit">Log in</button>
</form></body></html>""",
    "inventory.html": """<html><body>
<nav><a href="/inventory.html">Products</a><a id="cart" href="/cart.html">Cart</a></nav>
<h2>Welcome standard_user</h2>
<button data-testid="add-backpack">Add backpack</button>
<button>Profile standard_user</button>
<input placeholder="Search products" aria-label="Search">
</body></html>""",
    "cart.html": """<html><body>
<nav><a href="/inventory.html">Products</a><a id="cart" href="/cart.html">Cart</a></nav>
<button id="checkout">Checkout</button>
<a href="/inventory.html" id="continue">Continue shopping</a>
</body></html>""",
}


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def serve_site(directory: Path):
    for name, html in PAGES.items():
        (directory / name).write_text(html, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()

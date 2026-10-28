# tests/test_architecture.py
# Proves, by reading the source code, that the AI has no payment tool:
#   * only backend/agent/checkout.py may call paypal_service.create_order or capture_order
#   * only checkout.py may even import paypal_service
#   * the services layer (plumbing) never reaches into the payment path
#   * the AI-facing modules (voice, llm, understand, explain) never import the payment path
# The checks use Python's `ast` module, so comments and docstrings cannot fool them.
# scripts/ and tests/ are not scanned: they are run by a person, not by the app.

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCANNED_FOLDERS = ("backend", "database")

PAYMENT_FUNCTIONS = {"create_order", "capture_order"}
ONLY_CHECKOUT = "backend/agent/checkout.py"

# Modules that make up the payment path. AI-facing code must never import them.
PAYMENT_PATH_MODULES = {"checkout", "approvals", "step_up", "orders", "limits", "paypal_service"}
# Files that talk to an AI model or handle voice. (Some of these arrive in a later phase.)
AI_FACING_FILES = {"voice.py", "llm.py", "understand.py", "explain.py"}


# ---------- the detectors (tested below, so we know they really detect) ----------

def payment_function_references(source):
    """Every place the code mentions create_order or capture_order, as (line, name)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name) and node.id in PAYMENT_FUNCTIONS:
            found.append((line, node.id))
        elif isinstance(node, ast.Attribute) and node.attr in PAYMENT_FUNCTIONS:
            found.append((line, node.attr))
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in PAYMENT_FUNCTIONS:
            found.append((line, node.name))
        elif isinstance(node, ast.Constant) and node.value in PAYMENT_FUNCTIONS:    # getattr(x, "create_order")
            found.append((line, node.value))
    return found


def imported_names(source):
    """Every dotted name the code imports, e.g. {'backend.agent', 'backend.agent.checkout'}."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}".strip(".") for alias in node.names)
    return names


def imports_module(source, module_name):
    """True if the code imports a module whose last name part is module_name (absolute or relative)."""
    return any(name.split(".")[-1] == module_name for name in imported_names(source))


def imports_agent_layer(source):
    return any(name == "backend.agent" or name.startswith("backend.agent.") for name in imported_names(source))


# ---------- reading the project's files ----------

def project_files():
    files = {}
    for folder in SCANNED_FOLDERS:
        for path in sorted((ROOT / folder).rglob("*.py")):
            files[path.relative_to(ROOT).as_posix()] = path.read_text(encoding="utf-8")
    return files


FILES = project_files()


def test_the_scanner_really_finds_the_project_files():
    """If this fails, the other tests below would be scanning nothing and proving nothing."""
    assert len(FILES) >= 15
    assert ONLY_CHECKOUT in FILES
    assert "backend/services/paypal_service.py" in FILES


# ---------- the rules ----------

def test_only_checkout_calls_the_payment_functions():
    offenders = {path: payment_function_references(text)
                 for path, text in FILES.items() if path != ONLY_CHECKOUT}
    offenders = {path: refs for path, refs in offenders.items() if refs}
    assert offenders == {}, f"These files mention create_order/capture_order: {offenders}"


def test_checkout_really_is_where_payments_happen():
    """Guards the rule above: if payments moved elsewhere, the allow-list would be out of date."""
    names = {name for _, name in payment_function_references(FILES[ONLY_CHECKOUT])}
    assert names == PAYMENT_FUNCTIONS


def test_only_checkout_imports_paypal_service():
    importers = {path for path, text in FILES.items()
                 if path != "backend/services/paypal_service.py" and imports_module(text, "paypal_service")}
    assert importers == {ONLY_CHECKOUT}


def test_the_services_layer_never_imports_the_agent_layer():
    offenders = [path for path, text in FILES.items()
                 if path.startswith("backend/services/") and imports_agent_layer(text)]
    assert offenders == []


def test_ai_facing_modules_never_import_the_payment_path():
    offenders = {}
    for path, text in FILES.items():
        if Path(path).name in AI_FACING_FILES:
            bad = {module for module in PAYMENT_PATH_MODULES if imports_module(text, module)}
            if bad:
                offenders[path] = sorted(bad)
    assert offenders == {}, f"AI-facing code must not import the payment path: {offenders}"


# ---------- tests of the detectors themselves ----------

@pytest.mark.parametrize("snippet", [
    "paypal_service.create_order(items, key)",
    "paypal_service.capture_order(order_id, key)",
    "from backend.services.paypal_service import create_order",
    "from backend.services.paypal_service import capture_order as pay",
    "import backend.services.paypal_service.create_order",
    "fn = getattr(paypal_service, 'create_order')",
    "create_order(items, key)",
    "def sneaky():\n    return ps.capture_order('x', 'y')",
])
def test_the_payment_detector_catches_every_sneaky_form(snippet):
    assert payment_function_references(snippet), snippet


@pytest.mark.parametrize("snippet", [
    "# create_order is only mentioned in a comment",
    '"""A docstring that talks about create_order and capture_order."""',
    "print('we create an order here')",
    "order = build_order_body(items)",
    "created = my_create_order_helper(x)",
])
def test_the_payment_detector_ignores_comments_and_similar_names(snippet):
    assert payment_function_references(snippet) == []


@pytest.mark.parametrize("snippet", [
    "from backend.services import paypal_service",
    "import backend.services.paypal_service as pp",
    "from backend.services.paypal_service import LineItem",
    "from .services import paypal_service",
    "from .paypal_service import create_order",
])
def test_the_import_detector_catches_every_form(snippet):
    assert imports_module(snippet, "paypal_service"), snippet


def test_the_import_detector_ignores_other_modules():
    assert not imports_module("from backend.services import catalog", "paypal_service")
    assert not imports_module("import json", "paypal_service")


@pytest.mark.parametrize("snippet", [
    "from backend.agent import checkout",
    "import backend.agent.approvals",
    "from backend.agent.limits import is_paused",
    "from backend import agent",
])
def test_the_layer_detector_catches_agent_imports(snippet):
    assert imports_agent_layer(snippet), snippet
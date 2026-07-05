import pytest
from streamlit.testing.v1 import AppTest

def test_app_loads_correctly():
    """Testa che l'applicazione si avvii senza crash e mostri il titolo principale."""
    at = AppTest.from_file("app.py")
    # Ignoriamo i side effect dell'eventuale st.stop() dovuto alla mancanza di dataset
    at.run(timeout=10)
    
    # Verifica che la pagina contenga il titolo principale
    assert "Calcolo Tasse Fineco" in at.title[0].value

def test_app_sidebar_elements():
    """Testa che la sidebar sia presente e contenga gli elementi base."""
    at = AppTest.from_file("app.py").run(timeout=10)
    
    # Verifica il metodo di calcolo
    assert len(at.sidebar.radio) > 0
    assert "Metodo calcolo costo di carico" in at.sidebar.radio[0].label
    
    # Verifica il numero input minusvalenze
    assert len(at.sidebar.number_input) > 0
    assert "Minusvalenze pregresse" in at.sidebar.number_input[0].label

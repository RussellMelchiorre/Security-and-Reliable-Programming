"""Two simple smoke tests"""
import sqlite3
from setup_db import initialize
from repository import StoreRepository
from cart import ShoppingCart
from checkout import CheckoutService
from payments import FakePaymentGateway

def test_seed_product(tmp_path):
    db = tmp_path / 'test.db'
    initialize(str(db))
    assert StoreRepository(str(db)).get_product('MOU-01').name == 'Wireless mouse'


def test_cart_adds_valid_item(tmp_path):
    db = tmp_path / 'test.db'
    initialize(str(db))
    cart = ShoppingCart('student', StoreRepository(str(db)))
    assert cart.add_item('USB-01', 2)
    assert len(cart.lines) == 1


def test_update_stock_rejects_negative_stock(repo):
    with pytest.raises(sqlite3.IntegrityError):
        repo.update_stock('LAP-01', 6)
    assert repo.get_product('LAP-01').stock == 5


def test_list_orders_filters_by_user(repo):
    repo.save_order('alice', 29.95, 'paid', 'req-1')
    repo.save_order('bob', 999.99, 'paid', 'req-2')
    repo.save_order('alice', 9.99, 'declined', 'req-3')

    alice_orders = repo.list_orders('alice')
    bob_orders = repo.list_orders('bob')

    assert len(alice_orders) == 2
    assert all(order[1] == 'alice' for order in alice_orders)
    assert len(bob_orders) == 1
    assert bob_orders[0][1] == 'bob'


def test_get_product_prevents_sql_injection(repo):
    result = repo.get_product("' OR '1'='1")
    assert result is None


def test_get_saved_cart_prevents_sql_injection(repo):
    cart = ShoppingCart('alice', repo)
    cart.add_item('MOU-01', 2)
    cart.save()

    leaked = repo.get_saved_cart("' OR '1'='1")
    assert leaked == []


def test_save15_discount_does_not_go_negative(repo):
    cart = ShoppingCart('alice', repo)
    cart.add_item('USB-01', 1)

    assert cart.apply_discount('SAVE15') is True
    assert cart.total() == 0.0


def test_percentage_discounts_and_invalid_coupon(repo):
    cart = ShoppingCart('alice', repo)
    cart.add_item('MOU-01', 2)

    assert cart.apply_discount('INVALID_CODE') is False
    assert cart.total() == pytest.approx(59.90)

    assert cart.apply_discount('WELCOME10') is True
    assert cart.total() == pytest.approx(59.90 * 0.9)


def test_student20_discount_calculation(repo):
    cart = ShoppingCart('alice', repo)
    cart.add_item('LAP-01', 1)

    assert cart.apply_discount('STUDENT20') is True
    assert cart.total() == pytest.approx(999.99 * 0.8)


def test_repeated_load_does_not_duplicate_items(repo):
    cart = ShoppingCart('alice', repo)
    cart.add_item('MOU-01', 2)
    cart.save()

    reloaded = ShoppingCart('alice', repo)
    reloaded.load()
    reloaded.load()

    assert len(reloaded.lines) == 1
    assert reloaded.lines[0].sku == 'MOU-01'
    assert reloaded.lines[0].quantity == 2


def test_add_item_rejects_unknown_sku_and_excess_stock(repo):
    cart = ShoppingCart('alice', repo)

    assert cart.add_item('UNKNOWN-99', 1) is False
    assert cart.add_item('LAP-01', 10) is False
    assert cart.lines == []


def test_remove_item_partial_full_and_missing(repo):
    cart = ShoppingCart('alice', repo)
    cart.add_item('USB-01', 3)

    assert cart.remove_item('USB-01', 1) is True
    assert cart.lines[0].quantity == 2

    assert cart.remove_item('USB-01', 2) is True
    assert cart.lines == []

    assert cart.remove_item('USB-01') is False


def test_checkout_does_not_log_payment_token(repo, capsys):
    cart = ShoppingCart('alice', repo)
    cart.add_item('USB-01', 1)
    gateway = FakePaymentGateway(should_approve=True)
    secret_token = 'SECRET_TOK_12345'

    CheckoutService(repo, gateway).checkout(cart, secret_token, 'req-token-test')

    captured = capsys.readouterr()
    assert secret_token not in captured.out
    assert secret_token not in captured.err


def test_checkout_declined_payment_preserves_stock(repo):
    cart = ShoppingCart('alice', repo)
    cart.add_item('LAP-01', 2)
    gateway = FakePaymentGateway(should_approve=False)

    result = CheckoutService(repo, gateway).checkout(cart, 'TEST_TOKEN', 'req-declined')

    assert result.status == 'declined'
    assert repo.get_product('LAP-01').stock == 5


def test_checkout_payment_error_preserves_stock(repo):
    cart = ShoppingCart('alice', repo)
    cart.add_item('MOU-01', 3)

    result = CheckoutService(repo, FailingPaymentGateway()).checkout(
        cart, 'TEST_TOKEN', 'req-error'
    )

    assert result.status == 'payment_error'
    assert repo.get_product('MOU-01').stock == 20


def test_checkout_approved_updates_stock_and_saves_order(repo):
    cart = ShoppingCart('alice', repo)
    cart.add_item('MOU-01', 2)
    gateway = FakePaymentGateway(should_approve=True)

    result = CheckoutService(repo, gateway).checkout(cart, 'TEST_TOKEN', 'req-paid')

    assert result.status == 'paid'
    assert result.order_id is not None
    assert result.total == pytest.approx(59.90)
    assert repo.get_product('MOU-01').stock == 18
    assert len(gateway.calls) == 1
    assert len(repo.list_orders('alice')) == 1


def test_checkout_empty_cart_does_not_charge_gateway(repo):
    cart = ShoppingCart('alice', repo)
    gateway = FakePaymentGateway(should_approve=True)

    result = CheckoutService(repo, gateway).checkout(cart, 'TEST_TOKEN', 'req-empty')

    assert result.status == 'empty'
    assert result.order_id is None
    assert result.total == 0
    assert gateway.calls == []

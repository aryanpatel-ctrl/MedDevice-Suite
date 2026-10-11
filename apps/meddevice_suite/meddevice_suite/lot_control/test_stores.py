import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.lot_control import stores
from meddevice_suite.lot_control.stores import ROLE_FIELD, ROLES, apply_store_roles, role_of, store_for
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration

NEW_COMPANY, NEW_ABBR = "MDS Lot Test Company", "MLT"


def company() -> str:
	return frappe.defaults.get_global_default("company")


@verifies("URS-STK-005", "URS-STK-007")
class TestStoreRoles(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()

	def test_each_company_has_one_store_of_each_role(self):
		for role in ROLES:
			with self.subTest(role=role):
				store = store_for(role, company())
				self.assertEqual(role_of(store), role)
				self.assertEqual(frappe.db.get_value("Warehouse", store, "company"), company())
				self.assertFalse(frappe.db.get_value("Warehouse", store, "is_group"))

	def test_applying_again_adds_nothing(self):
		before = frappe.db.count("Warehouse")

		apply_store_roles()

		self.assertEqual(frappe.db.count("Warehouse"), before)

	def test_a_store_without_a_role_is_an_approved_store(self):
		stores_store = frappe.db.get_value("Warehouse", {"company": company(), "warehouse_name": "Stores"})

		self.assertIsNone(role_of(stores_store))

	def test_a_second_store_of_a_role_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as refused:
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": "Second Quarantine",
					"company": company(),
					"parent_warehouse": stores.root_store(company()),
					ROLE_FIELD: stores.QUARANTINE,
				}
			).insert()
		self.assertIn("already the Quarantine store", str(refused.exception))

	def test_a_group_cannot_carry_a_role(self):
		with self.assertRaises(frappe.ValidationError) as refused:
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": "Hold Group",
					"company": company(),
					"is_group": 1,
					"parent_warehouse": stores.root_store(company()),
					ROLE_FIELD: stores.HOLD,
				}
			).insert()
		self.assertIn("group of stores cannot be one", str(refused.exception))

	def test_renaming_a_role_store_keeps_its_role(self):
		store = store_for(stores.RETURNS, company())
		doc = frappe.get_doc("Warehouse", store)
		doc.warehouse_name = "Returns Area"
		doc.save()
		self.addCleanup(lambda: frappe.db.set_value("Warehouse", store, "warehouse_name", "Returns"))

		self.assertEqual(role_of(store), stores.RETURNS)

	def test_a_new_company_gets_its_role_stores_and_a_store_named_after_a_role_takes_it(self):
		if not frappe.db.exists("Company", NEW_COMPANY):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": NEW_COMPANY,
					"abbr": NEW_ABBR,
					"default_currency": "INR",
					"country": "India",
					"chart_of_accounts": "Standard",
				}
			).insert()
		# A store the company already named after a role, as the pilot's demo data does.
		if not frappe.db.exists("Warehouse", f"Quarantine - {NEW_ABBR}"):
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": "Quarantine",
					"company": NEW_COMPANY,
					"parent_warehouse": stores.root_store(NEW_COMPANY),
				}
			).insert()

		apply_store_roles()

		for role in ROLES:
			with self.subTest(role=role):
				self.assertEqual(store_for(role, NEW_COMPANY), f"{role} - {NEW_ABBR}")
		self.assertEqual(frappe.db.count("Warehouse", {"company": NEW_COMPANY, ROLE_FIELD: ["is", "set"]}), 4)

	def test_a_company_created_after_the_configuration_has_its_role_stores_at_once(self):
		# As on a new site, where the setup wizard creates the company after the app is
		# installed, or when a second company is added later: no migrate runs in between.
		name, abbr = "MDS Lot Late Company", "MLL"
		if not frappe.db.exists("Company", name):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": name,
					"abbr": abbr,
					"default_currency": "INR",
					"country": "India",
					"chart_of_accounts": "Standard",
				}
			).insert()

		for role in ROLES:
			with self.subTest(role=role):
				self.assertEqual(role_of(store_for(role, name)), role)

	def test_a_store_named_after_a_role_takes_the_role_when_none_holds_it(self):
		# A company that existed before the app was installed (cutover): its own "Quarantine"
		# store becomes the Quarantine store instead of a second one being made.
		store = store_for(stores.QUARANTINE, company())
		frappe.db.set_value("Warehouse", store, ROLE_FIELD, None)
		frappe.clear_document_cache("Warehouse", store)
		abbr = frappe.db.get_value("Company", company(), "abbr")
		self.assertEqual(store, f"{stores.QUARANTINE} - {abbr}")

		frappe.flags.in_migrate = True  # the configuration gives the role as the site migrates
		try:
			stores.ensure_role_stores(company(), abbr)
		finally:
			frappe.flags.in_migrate = False

		self.assertEqual(store_for(stores.QUARANTINE, company()), store)
		self.assertEqual(
			frappe.db.count("Warehouse", {"company": company(), ROLE_FIELD: stores.QUARANTINE}), 1
		)

	def test_an_existing_store_named_after_a_role_takes_it_even_with_stock_in_it(self):
		"""Cutover onto a site that already holds stock (found on the pilot, 2026-10-11): its own
		Quarantine store, with lots in it, becomes the Quarantine store; that is no change of what
		the store is for, so its stock does not stop it."""
		from meddevice_suite.lot_control.test_lot_status import RAW, lot_item, receive

		lot_item(RAW)
		store = store_for(stores.QUARANTINE, company())
		receive(RAW, 1, store)
		frappe.db.set_value("Warehouse", store, ROLE_FIELD, None)  # as before lot control
		frappe.clear_document_cache("Warehouse", store)
		abbr = frappe.db.get_value("Company", company(), "abbr")

		frappe.flags.in_migrate = True  # the configuration gives the role as the site migrates
		try:
			stores.ensure_role_stores(company(), abbr)
		finally:
			frappe.flags.in_migrate = False

		self.assertEqual(frappe.db.get_value("Warehouse", store, ROLE_FIELD), stores.QUARANTINE)

	def test_a_person_still_cannot_give_a_store_holding_stock_a_role(self):
		"""Only the system's first assignment to the store named after the role is let through."""
		from meddevice_suite.lot_control.test_lot_status import RAW, lot_item, receive

		lot_item(RAW)
		approved = frappe.db.get_value("Warehouse", {"company": company(), "warehouse_name": "Stores"})
		receive(RAW, 1, approved)
		quarantine = store_for(stores.QUARANTINE, company())
		frappe.db.set_value("Warehouse", quarantine, ROLE_FIELD, None)  # the role is free
		self.addCleanup(frappe.db.set_value, "Warehouse", quarantine, ROLE_FIELD, stores.QUARANTINE)
		doc = frappe.get_doc("Warehouse", approved)
		doc.set(ROLE_FIELD, stores.QUARANTINE)
		with self.assertRaisesRegex(frappe.ValidationError, "holds stock"):
			doc.save()

	def test_a_store_holding_stock_keeps_its_role(self):
		from meddevice_suite.lot_control.test_lot_status import RAW, lot_item, receive

		lot_item(RAW)
		hold = store_for(stores.HOLD, company())
		receive(RAW, 1, hold)
		doc = frappe.get_doc("Warehouse", hold)
		doc.set(ROLE_FIELD, "")

		with self.assertRaises(frappe.ValidationError) as refused:
			doc.save()
		self.assertIn("holds stock; move the stock", str(refused.exception))

	def test_a_missing_role_store_is_refused_plainly(self):
		with self.assertRaises(frappe.ValidationError) as refused:
			store_for(stores.HOLD, "No Such Company")
		self.assertIn("has no Hold store", str(refused.exception))

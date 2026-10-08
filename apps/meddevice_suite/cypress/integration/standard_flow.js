// The standard flow through the real screens, each step signed in as the job that does it
// (URS-PLT-007): quotation and its price approval, sales order, purchase order and its
// approval, goods receipt by lot into quarantine, the move to the approved store, a work
// order on its routing with the material issue, the operations on the job cards and the
// finished lot, and the delivery of that lot; then the cancellation of an approved order
// with its reason. The server test of the same flow (pilot/test_standard_flow.py) runs it
// through code; this one opens the forms, fills them in and presses the buttons, as the
// person in that job would, and fails on any refused request or error message the step did
// not expect (as the screen sweep does, screen_sweep.js).
//
// The masters are made on the test site by meddevice_suite/qa/ui_journey.py, which also
// writes off what an interrupted run left in store and puts in store, ahead of the run, a
// lot of a material and of the product that expire later than anything the run receives
// or makes: the run shows the lot that expires first is taken first.

const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const TIMEOUT = 20000; // the longest a page or an action may take
const QUIET_MS = 300; // no request in flight for this long: the page has finished

// ERPNext's refusal of a move that would take a lot below zero (serial_and_batch_bundle.py).
const NEGATIVE_LOT = /Batch No \S+ of an Item \S+ has negative stock/;

let password;
let users;
let data; // what the journey works with (ui_journey.prepare_journey)
const made = {}; // the records made along the way, by step

// What went wrong in the step: a refused or failed request, or an error message on screen,
// that the step did not expect. Every step ends by checking it is empty.
const run = { problems: [], expected: [] };

// --- Signing in -----------------------------------------------------------------------

// Signs in without writing the password into the command log or screenshots. The previous
// person's page is left first, as a person signs out before the next signs in: a request
// that page still made while the sessions change (a reload of its record, say) would go out
// with no one signed in, be refused, and be counted against this step.
function sign_in(job) {
	const email = users[job];
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	cy.session(["journey", email], () => {
		cy.request({
			url: "/api/method/login",
			method: "POST",
			body: { usr: email, pwd: password },
			log: false,
		});
	});
}

// --- Watching for problems ----------------------------------------------------------------

function plain(html) {
	return String(html ?? "")
		.replace(/<[^>]*>/g, " ")
		.replace(/\s+/g, " ")
		.trim();
}

function parsed(body) {
	if (!body || typeof body === "object") return body || {};
	try {
		return JSON.parse(body);
	} catch {
		return {};
	}
}

function server_message(body) {
	const response = parsed(body);
	const messages = [response.exc_type].filter(Boolean);
	try {
		for (const message of JSON.parse(response._server_messages || "[]")) {
			messages.push(plain(JSON.parse(message).message));
		}
	} catch {
		// not Frappe's message list: the status says enough
	}
	return messages.join(": ");
}

function watch_requests() {
	cy.intercept({ url: API }, (req) => {
		req.on("response", (res) => {
			if (res.statusCode < 400) return;
			const message = server_message(res.body);
			const expected = run.expected.findIndex((pattern) => pattern.test(message));
			if (expected >= 0) {
				run.expected.splice(expected, 1);
			} else {
				const path = new URL(req.url).pathname;
				run.problems.push(`${req.method} ${res.statusCode} ${path} → ${message}`);
			}
		});
	});
}

// The next refused request whose message matches is the one the step is about.
function expect_refusal(pattern) {
	cy.then(() => run.expected.push(pattern));
}

// Resolves true once `ready()` holds, false when it does not within the timeout.
function until(ready, timeout = TIMEOUT) {
	return new Promise((resolve) => {
		const started = Date.now();
		const check = () => {
			let done = false;
			try {
				done = Boolean(ready());
			} catch {
				done = false;
			}
			if (done) return resolve(true);
			if (Date.now() - started > timeout) return resolve(false);
			setTimeout(check, 50);
		};
		check();
	});
}

// Waits until no request has been in flight for QUIET_MS and the screen is not frozen.
function settle() {
	return cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) => {
		let since = null;
		return until(() => {
			if (win.frappe.request.ajax_count > 0 || win.frappe.dom.freeze_count > 0) {
				since = null;
				return false;
			}
			since = since ?? Date.now();
			return Date.now() - since >= QUIET_MS;
		}).then(
			(settled) =>
				expect(
					settled,
					`the page finished what it was doing (requests in flight: ${win.frappe.request.ajax_count}, screen frozen: ${win.frappe.dom.freeze_count})`
				).to.be.true
		);
	});
}

function message_dialog(win) {
	const dialog = win.frappe.msg_dialog;
	if (!dialog || !dialog.$wrapper.is(":visible")) return null;
	return {
		dialog,
		text: plain(
			`${dialog.$wrapper.find(".modal-title").text()}: ${dialog.$wrapper
				.find(".msgprint")
				.text()}`
		),
		// Frappe marks errors and refusals red and warnings orange.
		error: /\b(red|orange)\b/.test(dialog.indicator?.attr("class") || ""),
	};
}

function close_message(win) {
	return until(() => {
		if (!win.frappe.msg_dialog.$wrapper.is(":visible")) return true;
		win.frappe.hide_msgprint(true);
		return false;
	}, 5000);
}

// The step went as expected: no error message on screen, no unexpected refusal, and every
// refusal the step expected has happened.
function no_problems(step) {
	settle();
	cy.window({ log: false }).then((win) => {
		const shown = message_dialog(win);
		if (shown?.error) run.problems.push(`the message "${shown.text}" is shown`);
		const problems = run.problems.splice(0);
		const missing = run.expected.splice(0);
		expect(problems, `${step}: problems\n${problems.join("\n")}\n`).to.be.empty;
		expect(missing.map(String), `${step}: refusals expected but not made`).to.be.empty;
	});
}

// The step's refusal is on screen as an error message saying `pattern`; it is closed.
function refused_with(pattern) {
	cy.window()
		.should((win) => {
			const shown = message_dialog(win);
			expect(shown, "an error message").to.not.equal(null);
			expect(shown.error, `"${shown.text}" is an error`).to.be.true;
			expect(shown.text).to.match(pattern);
		})
		.then({ timeout: 10000 }, (win) =>
			close_message(win).then((closed) => expect(closed, "the message closes").to.be.true)
		);
}

// --- Using the desk -----------------------------------------------------------------------

function slug(doctype) {
	return doctype.toLowerCase().replace(/ /g, "-");
}

function open_form(doctype, name) {
	cy.visit(`/desk/${slug(doctype)}/${encodeURIComponent(name)}`);
	form_is(doctype, (doc) => doc.name === name);
	settle();
}

function new_form(doctype) {
	cy.visit(`/desk/${slug(doctype)}/new`);
	form_is(doctype, (doc) => doc.__islocal);
	settle();
}

// Waits for the form of `doctype` to show a record for which `holds(doc)` is true.
function form_is(doctype, holds = () => true) {
	cy.window().should((win) => {
		const frm = win.cur_frm;
		expect(win.frappe.get_route()[0], "a form is open").to.equal("Form");
		expect(frm?.doctype, "the form").to.equal(doctype);
		expect(frm.doc.name, "the record shown").to.equal(win.frappe.get_route()[2]);
		expect(Boolean(holds(frm.doc)), "the record is as expected").to.be.true;
	});
}

function current(win) {
	return win.cur_frm.doc;
}

// Brings a field into view as the person would: its tab opened, its section expanded.
function reveal(fieldname) {
	cy.window().then((win) => {
		const field = win.cur_frm.get_field(fieldname);
		if (field.tab && !field.tab.is_active()) {
			cy.get(`${FORM} .form-tabs-list [data-fieldname="${field.tab.df.fieldname}"]`).click();
		}
		if (field.section?.is_collapsed?.()) {
			field.section.head.trigger("click");
		}
	});
	cy.get(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`).first().scrollIntoView();
}

// The inputs a person can type into or choose from inside `container`.
function inputs_of(container) {
	return `${container} input:visible, ${container} select:visible`;
}

function user_date(win, value) {
	return win.frappe.datetime.str_to_user(value);
}

// Types into a field's input and leaves it, as the person would.
function type_into($input, text) {
	cy.wrap($input).clear().type(text, { delay: 0 });
	// A date picker may already have taken the focus away.
	cy.document().then((doc) => {
		if (doc.activeElement === $input[0]) cy.wrap($input).blur();
	});
}

// Types into a link field and chooses the matching entry of its list.
function choose($input, value) {
	cy.wrap($input).clear().focus().type(value, { delay: 0 });
	// The open list of the field: only one is open at a time.
	cy.get('[role="listbox"]:visible [role="option"]')
		.filter((_, option) => option.querySelector("strong")?.textContent.trim() === value)
		.first()
		.click();
}

function fill(fieldname, value, kind = "Data") {
	reveal(fieldname);
	cy.get(inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`))
		.first()
		.then(($input) =>
			cy.window().then((win) => {
				if (kind === "Link") choose($input, value);
				else if (kind === "Select") cy.wrap($input).select(value);
				else if (kind === "Date") type_into($input, user_date(win, value));
				else type_into($input, String(value));
			})
		);
	cy.window().should((win) => {
		const stored = current(win)[fieldname];
		if (typeof value === "number") expect(stored, fieldname).to.be.closeTo(value, 1e-6);
		else expect(stored, fieldname).to.equal(value);
	});
}

function rows(win, table) {
	return current(win)[table] || [];
}

// Makes sure the table has `count` rows, adding them with its Add Row button.
function rows_up_to(table, count) {
	cy.window().then((win) => {
		const missing = count - rows(win, table).length;
		for (let i = 0; i < missing; i++) {
			cy.get(`${FORM} .frappe-control[data-fieldname="${table}"] .grid-add-row`).click();
		}
	});
	cy.window().should((win) => expect(rows(win, table)).to.have.length(count));
}

function fill_row(table, idx, fieldname, value, kind = "Data") {
	const cell = `${FORM} .frappe-control[data-fieldname="${table}"] .grid-row[data-idx="${idx}"] [data-fieldname="${fieldname}"]`;
	cy.get(cell).first().click();
	cy.get(inputs_of(cell))
		.first()
		.then(($input) =>
			cy.window().then((win) => {
				if (kind === "Link") choose($input, value);
				else if (kind === "Datetime") type_into($input, user_date(win, value));
				else type_into($input, String(value));
			})
		);
	cy.window().should((win) => {
		const stored = rows(win, table)[idx - 1][fieldname];
		if (typeof value === "number") expect(stored, fieldname).to.be.closeTo(value, 1e-6);
		else if (kind === "Datetime")
			expect(stored?.slice(0, 16), fieldname).to.equal(value.slice(0, 16));
		else expect(stored, fieldname).to.equal(value);
	});
}

function button(label) {
	return cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]:visible`).first();
}

function offered(label, expected) {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]:visible`).should(
		expected ? "exist" : "not.exist"
	);
}

function from_menu(group, label) {
	const menu = `${FORM} .inner-group-button[data-label="${encodeURIComponent(group)}"]`;
	cy.get(`${menu} > button`).click();
	cy.get(`${menu} .dropdown-item[data-label="${encodeURIComponent(label)}"]`).click();
}

function workflow_actions() {
	settle();
	return cy.get(`${FORM} .actions-btn-group`);
}

function take_action(action) {
	workflow_actions().find("> button").click();
	cy.get(`${FORM} .actions-btn-group .dropdown-menu a.dropdown-item`)
		.contains(new RegExp(`^\\s*${action}\\s*$`))
		.click();
}

function actions_offered(win) {
	return win
		.$(`${FORM} .actions-btn-group .dropdown-menu a.dropdown-item`)
		.toArray()
		.map((item) => item.textContent.trim());
}

function primary_action() {
	return cy.get(`${FORM} .page-actions .primary-action:visible`);
}

function save() {
	primary_action().should("contain", "Save").click();
	cy.window().should((win) => {
		const frm = win.cur_frm;
		expect(frm.is_new(), "saved").to.not.be.ok;
		expect(frm.is_dirty(), "nothing left unsaved").to.not.be.ok;
	});
	settle();
}

// Waits for the dialog that has just opened to finish opening (it takes focus when its fade
// ends), as a person would before using it.
function open_dialog() {
	return cy.get(".modal.show").should("have.css", "opacity", "1").last();
}

function submit() {
	primary_action().should("contain", "Submit").click();
	open_dialog().should("contain", "Permanently Submit");
	cy.get(".modal.show .btn-primary").contains("Yes").click();
	cy.window().should((win) => expect(current(win).docstatus, "submitted").to.equal(1));
	settle();
}

// The record as stored, read as the signed-in user.
function stored(doctype, name) {
	return cy.call("frappe.client.get", { doctype, name }).then((r) => r.message);
}

function lots_of(doctype, name) {
	return cy.call(`${JOURNEY}.lots_of`, { doctype, name }).then((r) => r.message);
}

// A server call the signed-in user makes that must be refused; returns the refusal.
function refused_call(method, args) {
	return cy
		.window()
		.its("frappe.csrf_token")
		.then((csrf_token) =>
			cy.request({
				method: "POST",
				url: `/api/method/${method}`,
				body: args,
				headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrf_token },
				failOnStatusCode: false,
			})
		)
		.then((response) => {
			expect(response.status, "refused").to.be.within(400, 499);
			return server_message(response.body);
		});
}

function in_days(win, days) {
	return win.frappe.datetime.add_days(win.frappe.datetime.get_today(), days);
}

// --- The journey ----------------------------------------------------------------------------

// Each step builds on the one before, so a step is never tried again (its first attempt may
// have done part of its work), and once one fails the steps after it are not run.
context("The standard flow, through the screens of each job", { retries: 0 }, () => {
	let stopped = false;

	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		cy.call(`${JOURNEY}.prepare_journey`).then(({ message }) => {
			data = message;
		});
	});

	beforeEach(function () {
		if (stopped) this.skip();
		run.problems.length = 0;
		run.expected.length = 0;
		watch_requests();
	});

	afterEach(function () {
		if (this.currentTest.state === "failed") stopped = true;
	});

	it("[URS-PLT-007] [URS-CMP-019] [URS-CMP-021] Sales quotes the product and sends it for price approval, which Sales is not offered", () => {
		sign_in("Sales");
		new_form("Quotation");
		fill("party_name", data.customer, "Link");
		rows_up_to("items", 1);
		fill_row("items", 1, "item_code", data.product, "Link");
		fill_row("items", 1, "qty", data.quantity);
		fill_row("items", 1, "rate", data.rate);
		save();
		take_action("Send for Price Approval");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Awaiting Price Approval");
			expect(current(win).docstatus).to.equal(0);
		});
		cy.window().then((win) => {
			made.quotation = current(win).name;
		});
		// Approving is not Sales' to do: no action is offered at all.
		workflow_actions().should("not.be.visible");
		cy.window().then((win) => expect(actions_offered(win)).to.not.include("Approve"));
		no_problems("Sales quotes");
	});

	it("[URS-PLT-007] [URS-CMP-019] [URS-CMP-021] Management approves the price; the approved quotation's items and prices cannot be changed", () => {
		sign_in("Management");
		open_form("Quotation", made.quotation);
		take_action("Approve");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Approved");
			expect(current(win).docstatus).to.equal(1);
		});

		// ERPNext offers Update Items on a submitted quotation; a new rate is refused.
		button("Update Items").click();
		open_dialog().should("contain", "Update Items");
		const rate =
			'.modal.show [data-fieldname="trans_items"] .grid-row[data-idx="1"] [data-fieldname="rate"]';
		cy.get(rate).first().click();
		cy.get(`${rate} input:visible`)
			.first()
			.clear()
			.type(String(data.rate + 7))
			.blur();
		cy.window()
			.its("cur_dialog")
			.should((dialog) =>
				expect(dialog.get_value("trans_items")[0].rate).to.equal(data.rate + 7)
			);
		expect_refusal(/has been approved; its items, quantities and prices cannot change/);
		cy.get(".modal.show .btn-primary").contains("Update").click();
		refused_with(/has been approved; its items, quantities and prices cannot change/);
		stored("Quotation", made.quotation).then((quotation) => {
			expect(quotation.items[0].rate).to.equal(data.rate);
			expect(quotation.workflow_state).to.equal("Approved");
		});
		no_problems("Management approves the price");
	});

	it("[URS-PLT-007] Sales books the sales order from the approved quotation", () => {
		sign_in("Sales");
		open_form("Quotation", made.quotation);
		from_menu("Create", "Sales Order");
		form_is("Sales Order", (doc) => doc.__islocal);
		settle();
		cy.window().then((win) => fill("delivery_date", in_days(win, 21), "Date"));
		fill_row("items", 1, "qty", data.quantity);
		save();
		submit();
		cy.window().then((win) => {
			made.sales_order = current(win).name;
			expect(current(win).items[0].prevdoc_docname).to.equal(made.quotation);
			expect(current(win).items[0].rate).to.equal(data.rate);
		});
		no_problems("Sales books the order");
	});

	it("[URS-PLT-007] [URS-CMP-020] [URS-CMP-021] Purchase orders the raw materials and sends the order for approval, then cannot change it", () => {
		sign_in("Purchase");
		new_form("Purchase Order");
		fill("supplier", data.supplier, "Link");
		cy.window().then((win) => fill("schedule_date", in_days(win, 7), "Date"));
		fill("set_warehouse", data.stores.quarantine, "Link");
		const materials = Object.entries(data.materials);
		materials.forEach(([item, qty], index) => {
			rows_up_to("items", index + 1);
			fill_row("items", index + 1, "item_code", item, "Link");
			fill_row("items", index + 1, "qty", qty);
		});
		save();
		take_action("Send for Approval");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Awaiting Approval");
			expect(current(win).docstatus).to.equal(0);
			made.purchase_order = current(win).name;
		});
		workflow_actions().should("not.be.visible");

		// Awaiting approval, the order is the approver's: the form shows Purchase its lines
		// read-only, and the server refuses a changed rate sent all the same.
		cy.get(
			`${FORM} .frappe-control[data-fieldname="items"] .grid-row[data-idx="1"] .row-index`
		)
			.first()
			.click();
		cy.get(".grid-row-open").should("be.visible").and("contain", "Rate");
		cy.get('.grid-row-open .frappe-control[data-fieldname="rate"]').should("be.visible");
		cy.get('.grid-row-open .frappe-control[data-fieldname="rate"] input:visible').should(
			"not.exist"
		);
		cy.get(".grid-form-heading:visible").first().click(); // closes the line again
		cy.get(".grid-row-open").should("not.exist");
		primary_action().should("not.exist");
		cy.window().then((win) => {
			const row = current(win).items[0];
			refused_call("frappe.client.set_value", {
				doctype: "Purchase Order Item",
				name: row.name,
				fieldname: "rate",
				value: row.rate + 1,
			}).should("match", /is Awaiting Approval; only Purchase Manager can change it now/);
		});
		cy.then(() => stored("Purchase Order", made.purchase_order)).then((order) =>
			expect(order.workflow_state).to.equal("Awaiting Approval")
		);
		no_problems("Purchase orders");
	});

	it("[URS-PLT-007] [URS-CMP-020] Management approves the purchase order", () => {
		sign_in("Management");
		open_form("Purchase Order", made.purchase_order);
		take_action("Approve");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Approved");
			expect(current(win).docstatus).to.equal(1);
		});
		no_problems("Management approves the order");
	});

	it("[URS-PLT-007] Stores receives the order into quarantine; each material gets a new lot with its expiry", () => {
		sign_in("Stores");
		open_form("Purchase Order", made.purchase_order);
		from_menu("Create", "Purchase Receipt");
		form_is("Purchase Receipt", (doc) => doc.__islocal);
		settle();
		cy.window().should((win) =>
			current(win).items.forEach((row) =>
				expect(row.warehouse, row.item_code).to.equal(data.stores.quarantine)
			)
		);
		save();
		submit();
		cy.window().then((win) => {
			made.receipt = current(win).name;
			lots_of("Purchase Receipt", made.receipt).then((lots) => {
				expect(lots.map((lot) => lot.item_code)).to.deep.equal(
					Object.keys(data.materials)
				);
				made.received_lots = {};
				for (const lot of lots) {
					expect(lot.to_store, lot.item_code).to.equal(data.stores.quarantine);
					expect(lot.batch_no, `${lot.item_code} lot`).to.be.ok;
					expect(lot.batch_no, `${lot.item_code} lot is new`).to.not.equal(
						data.lots_in_store[lot.item_code]
					);
					expect(lot.expiry_date, `${lot.item_code} expiry`).to.be.ok;
					expect(lot.qty).to.equal(data.materials[lot.item_code]);
					made.received_lots[lot.item_code] = lot.batch_no;
				}
			});
		});
		no_problems("Stores receives");
	});

	it("[URS-PLT-007] [URS-STK-003] Stores moves the lots to the approved store; moving them again is refused, for none is left", () => {
		sign_in("Stores");
		open_form("Purchase Receipt", made.receipt);
		from_menu("Create", "Make Stock Entry");
		form_is("Stock Entry", (doc) => doc.__islocal && doc.purpose === "Material Transfer");
		settle();
		fill("to_warehouse", data.stores.approved, "Link");
		cy.window().should((win) =>
			current(win).items.forEach((row) => {
				expect(row.s_warehouse, row.item_code).to.equal(data.stores.quarantine);
				expect(row.t_warehouse, row.item_code).to.equal(data.stores.approved);
				expect(row.batch_no, row.item_code).to.equal(made.received_lots[row.item_code]);
			})
		);
		save();
		submit();
		cy.window().then((win) => {
			made.transfer = current(win).name;
		});
		cy.then(() => lots_of("Stock Entry", made.transfer)).then((lots) =>
			lots.forEach((lot) => {
				expect(lot.batch_no).to.equal(made.received_lots[lot.item_code]);
				expect(lot.to_store).to.equal(data.stores.approved);
			})
		);

		// The same lots again: quarantine holds none of them any more.
		open_form("Purchase Receipt", made.receipt);
		from_menu("Create", "Make Stock Entry");
		form_is("Stock Entry", (doc) => doc.__islocal && doc.purpose === "Material Transfer");
		settle();
		fill("to_warehouse", data.stores.approved, "Link");
		save(); // a draft moves nothing; the stock is checked when it is submitted
		expect_refusal(NEGATIVE_LOT);
		primary_action().should("contain", "Submit").click();
		open_dialog().should("contain", "Permanently Submit");
		cy.get(".modal.show .btn-primary").contains("Yes").click();
		refused_with(NEGATIVE_LOT);
		cy.window().should((win) => expect(current(win).docstatus).to.equal(0));
		cy.then(() =>
			cy.call("frappe.client.get_list", {
				doctype: "Stock Entry",
				fields: ["name"],
				filters: [
					["Stock Entry", "docstatus", "=", 1],
					["Stock Entry Detail", "s_warehouse", "=", data.stores.quarantine],
					["Stock Entry Detail", "batch_no", "in", Object.values(made.received_lots)],
				],
				limit_page_length: 0,
			})
		).then(({ message }) =>
			expect(
				[...new Set(message.map((entry) => entry.name))],
				"moves of these lots out of quarantine"
			).to.deep.equal([made.transfer])
		);
		no_problems("Stores moves the lots");
	});

	it("[URS-PLT-007] [URS-STK-002] Production plans the work order on its routing and starts it; the lots that expire first are issued", () => {
		sign_in("Production Supervisor");
		new_form("Work Order");
		fill("production_item", data.product, "Link");
		cy.window().should((win) => expect(current(win).bom_no, "default BOM").to.equal(data.bom));
		fill("qty", data.quantity);
		fill("sales_order", made.sales_order, "Link");
		fill("source_warehouse", data.stores.approved, "Link");
		fill("wip_warehouse", data.stores.work_in_progress, "Link");
		fill("fg_warehouse", data.stores.finished_goods, "Link");
		cy.window().should((win) =>
			expect(
				current(win).operations.map((row) => row.operation),
				"routing"
			).to.deep.equal(data.operations)
		);
		save();
		submit();
		cy.window().then((win) => {
			made.work_order = current(win).name;
		});
		// ERPNext makes a job card per operation of the routing when the order is submitted.
		cy.then(() =>
			cy.call("frappe.client.get_list", {
				doctype: "Job Card",
				filters: { work_order: made.work_order },
				fields: ["name", "operation", "for_quantity", "docstatus"],
				order_by: "sequence_id asc, creation asc",
			})
		).then(({ message: cards }) => {
			expect(cards.map((card) => card.operation)).to.deep.equal(data.operations);
			cards.forEach((card) => {
				expect(card.for_quantity).to.equal(data.quantity);
				expect(card.docstatus).to.equal(0);
			});
			made.job_cards = cards.map((card) => card.name);
		});

		button("Start").click();
		open_dialog().should("contain", "Select Quantity");
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("qty")).to.equal(data.quantity));
		cy.get(".modal.show .btn-primary").contains("Create").click();
		form_is(
			"Stock Entry",
			(doc) => doc.__islocal && doc.purpose === "Material Transfer for Manufacture"
		);
		settle();
		save();
		submit();
		cy.window().then((win) => {
			made.issue = current(win).name;
		});
		// Nobody chose the lots: ERPNext picked them, the one that expires first of each
		// material, although an older lot of silicone was already in the store.
		cy.then(() => lots_of("Stock Entry", made.issue)).then((lots) => {
			expect(lots.map((lot) => lot.item_code).sort()).to.deep.equal(
				Object.keys(data.materials).sort()
			);
			for (const lot of lots) {
				expect(lot.from_store, lot.item_code).to.equal(data.stores.approved);
				expect(lot.to_store, lot.item_code).to.equal(data.stores.work_in_progress);
				expect(lot.batch_no, `${lot.item_code} lot`).to.equal(
					made.received_lots[lot.item_code]
				);
				expect(lot.qty).to.equal(data.materials[lot.item_code]);
			}
		});
		no_problems("Production starts the order");
	});

	it("[URS-PLT-007] An operator logs the first operation, without being able to submit it; the supervisor submits it", () => {
		sign_in("Operator");
		cy.then(() => open_form("Job Card", made.job_cards[0]));
		reveal("time_logs");
		rows_up_to("time_logs", 1);
		// The time the operation was planned for, which no other job card on the workstation holds.
		cy.window().then((win) => {
			const card = current(win);
			const planned = card.scheduled_time_logs[0];
			fill_row("time_logs", 1, "from_time", planned.from_time.slice(0, 19), "Datetime");
			fill_row("time_logs", 1, "to_time", planned.to_time.slice(0, 19), "Datetime");
			fill_row("time_logs", 1, "completed_qty", card.for_quantity);
		});
		save();
		cy.get(`${FORM} .page-actions`).should(($actions) =>
			expect($actions.find(".primary-action:visible").text()).to.not.contain("Submit")
		);
		cy.window().should((win) => {
			expect(current(win).docstatus).to.equal(0);
			expect(current(win).total_completed_qty).to.equal(data.quantity);
		});
		no_problems("The operator logs the operation");

		sign_in("Production Supervisor");
		cy.then(() => open_form("Job Card", made.job_cards[0]));
		submit();
		cy.window().should((win) => expect(current(win).status).to.equal("Completed"));
		no_problems("The supervisor submits the job card");

		// The other operations are logged and submitted by the same two people through the API,
		// to keep the run short: the screens are the ones just used.
		cy.then(() =>
			made.job_cards.slice(1).forEach((name) => {
				sign_in("Operator");
				cy.visit("/desk");
				cy.call("frappe.client.get", { doctype: "Job Card", name }).then(({ message }) => {
					const planned = message.scheduled_time_logs[0];
					message.time_logs = [
						{
							from_time: planned.from_time,
							to_time: planned.to_time,
							completed_qty: message.for_quantity,
						},
					];
					cy.call("frappe.client.save", { doc: message });
				});
				sign_in("Production Supervisor");
				cy.visit("/desk");
				cy.call("frappe.client.get", { doctype: "Job Card", name }).then(({ message }) =>
					cy.call("frappe.client.submit", { doc: message })
				);
			})
		);
		cy.then(() =>
			cy.call("frappe.client.get_list", {
				doctype: "Job Card",
				filters: { work_order: made.work_order },
				fields: ["status", "docstatus"],
			})
		).then(({ message: cards }) =>
			cards.forEach((card) => {
				expect(card.docstatus).to.equal(1);
				expect(card.status).to.equal("Completed");
			})
		);
		no_problems("The operations are done");
	});

	it("[URS-PLT-007] Production finishes the work order; the finished lot is made with its expiry date", () => {
		sign_in("Production Supervisor");
		cy.then(() => open_form("Work Order", made.work_order));
		button("Finish").click();
		open_dialog().should("contain", "Select Quantity");
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("qty")).to.equal(data.quantity));
		cy.get(".modal.show .btn-primary").contains("Create").click();
		form_is("Stock Entry", (doc) => doc.__islocal && doc.purpose === "Manufacture");
		settle();
		save();
		submit();
		cy.window().then((win) => {
			made.manufacture = current(win).name;
			made.expected_expiry = in_days(win, data.product_shelf_life_days);
		});
		cy.then(() => lots_of("Stock Entry", made.manufacture)).then((lots) => {
			const finished = lots.filter((lot) => lot.item_code === data.product);
			expect(finished, "finished lots").to.have.length(1);
			const [lot] = finished;
			expect(lot.to_store).to.equal(data.stores.finished_goods);
			expect(lot.qty).to.equal(data.quantity);
			expect(lot.batch_no).to.be.ok.and.not.equal(data.lots_in_store[data.product]);
			expect(lot.expiry_date, "expiry date").to.equal(made.expected_expiry);
			made.finished_lot = lot.batch_no;
		});
		cy.then(() => stored("Work Order", made.work_order)).then((order) => {
			expect(order.status).to.equal("Completed");
			expect(order.produced_qty).to.equal(data.quantity);
		});
		no_problems("Production finishes");
	});

	it("[URS-PLT-007] [URS-STK-002] Stores delivers the order; the lot delivered is the one just made, which expires first", () => {
		sign_in("Stores");
		cy.then(() => open_form("Sales Order", made.sales_order));
		from_menu("Create", "Delivery Note");
		form_is("Delivery Note", (doc) => doc.__islocal);
		settle();
		cy.window().should((win) =>
			current(win).items.forEach((row) => {
				expect(row.item_code).to.equal(data.product);
				expect(row.qty).to.equal(data.quantity);
				expect(row.warehouse).to.equal(data.stores.finished_goods);
			})
		);
		save();
		submit();
		cy.window().then((win) => {
			made.delivery = current(win).name;
		});
		// The finished-goods store also holds an older lot that expires later; it stays.
		cy.then(() => lots_of("Delivery Note", made.delivery)).then((lots) =>
			expect(lots.map((lot) => [lot.batch_no, lot.qty])).to.deep.equal([
				[made.finished_lot, data.quantity],
			])
		);
		cy.then(() => stored("Sales Order", made.sales_order)).then((order) =>
			expect(order.per_delivered).to.equal(100)
		);
		no_problems("Stores delivers");
	});

	it("[URS-PLT-007] [URS-CMP-011] [URS-CMP-020] Management cancels an approved purchase order only with a reason, which the audit trail keeps", () => {
		const reason = "Supplier cannot deliver this lot (browser journey)";
		// A small order of its own, raised and approved through the API by the two jobs.
		sign_in("Purchase");
		cy.visit("/desk");
		cy.window().then((win) =>
			cy
				.call("frappe.client.insert", {
					doc: {
						doctype: "Purchase Order",
						supplier: data.supplier,
						schedule_date: in_days(win, 7),
						set_warehouse: data.stores.quarantine,
						items: [
							{
								item_code: Object.keys(data.materials)[1],
								qty: 1,
								schedule_date: in_days(win, 7),
								warehouse: data.stores.quarantine,
							},
						],
					},
				})
				.then(({ message: order }) => {
					made.cancelled_order = order.name;
					cy.call("frappe.model.workflow.apply_workflow", {
						doc: order,
						action: "Send for Approval",
					});
				})
		);
		sign_in("Management");
		cy.visit("/desk");
		cy.then(() =>
			cy.call("frappe.model.workflow.apply_workflow", {
				doc: { doctype: "Purchase Order", name: made.cancelled_order },
				action: "Approve",
			})
		);
		cy.then(() => open_form("Purchase Order", made.cancelled_order));
		cy.window().should((win) => expect(current(win).workflow_state).to.equal("Approved"));

		take_action("Cancel");
		cy.then(() =>
			open_dialog().should("contain", `Reason for cancelling ${made.cancelled_order}`)
		);
		// Without a reason the dialog does not go on.
		cy.get(".modal.show .btn-primary").contains("Continue").click();
		refused_with(/Missing Values Required.*Reason/);
		open_dialog().find('textarea[data-fieldname="reason"]').should("have.value", "");
		cy.window().should((win) => expect(current(win).docstatus).to.equal(1));

		open_dialog().find('textarea[data-fieldname="reason"]').click().type(reason);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
		cy.get(".modal.show .btn-primary").contains("Continue").click();
		cy.window().should((win) => {
			expect(current(win).docstatus).to.equal(2);
			expect(current(win).workflow_state).to.equal("Cancelled");
		});
		no_problems("Management cancels the order");

		// Management may not read the audit trail; a reviewer checks it.
		cy.login();
		cy.visit("/desk");
		cy.then(() =>
			cy.call("frappe.client.get_list", {
				doctype: "Audit Event",
				filters: {
					reference_doctype: "Purchase Order",
					reference_name: made.cancelled_order,
					action: "Cancelled",
				},
				fields: ["user", "reason"],
			})
		).then(({ message }) =>
			expect(message).to.deep.equal([{ user: users.Management, reason }])
		);
		// The journey's regulated records are in the audit trail too, by the jobs that made them.
		cy.then(() =>
			cy.call("frappe.client.get_list", {
				doctype: "Audit Event",
				filters: {
					action: "Submitted",
					reference_name: [
						"in",
						[
							made.receipt,
							made.transfer,
							made.work_order,
							made.manufacture,
							made.delivery,
						],
					],
				},
				fields: ["reference_doctype", "reference_name", "user"],
				limit_page_length: 0,
			})
		).then(({ message }) => {
			const by = Object.fromEntries(
				message.map((event) => [event.reference_name, event.user])
			);
			expect(by).to.deep.equal({
				[made.receipt]: users.Stores,
				[made.transfer]: users.Stores,
				[made.work_order]: users["Production Supervisor"],
				[made.manufacture]: users["Production Supervisor"],
				[made.delivery]: users.Stores,
			});
		});
	});
});

// The browser test sheet of data store D1 Customer Master (URS-MST-008, URS-MST-001), one test
// per row of the sheet, in its order, each signed in as the job the sheet names and done on the
// screens as that person does it: Sales records a customer and its licences, QA verifies, rejects
// and withdraws them and decides with a signature that a customer buys without a licence, sales
// orders and deliveries are refused or made accordingly, and a customer in use changes only under
// an approved change request (master_data/customer_licence.py, master_data/versioning.py).
//
// The rows build on each other, so a row is never tried again and once one fails the rows after
// it are not run (as standard_flow.js), except D1-T15 and D1-T16, which start from records of their
// own and run whatever the rows before them did: QA reads the audit trail of a customer and its
// licence, and a licence is never deleted or cancelled, not even by the Administrator. The job
// users and the standard flow's product and stock come from meddevice_suite/qa/ui_users.py and
// ui_journey.py; the customers the sheet starts from (an exempt hospital, a licensed distributor,
// and those of D1-T15 and D1-T16) from meddevice_suite/qa/sheets/d1.py.

const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const SHEET = "meddevice_suite.qa.sheets.d1";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const TIMEOUT = 20000; // the longest a page or an action may take
const QUIET_MS = 300; // no request in flight for this long: the page has finished
const RELOAD_DELAY_MS = 1000; // the desk reloads a form this long after the server changes it

const EXEMPT = "mds_buys_without_licence";
const STATUS_FIELDS = ["status", "reason", "decided_by", "decided_at"];
const NOT_LICENSED_BANNER =
	"No licence verified by QA is valid today: no sales order or delivery is made for this customer.";
const AWAITING_BANNER =
	"Awaiting QA's verification: no sales order or delivery is made on it yet.";
const EXEMPT_BANNER = "Buys without a licence, as QA decided with a signature.";
const NOT_LICENSED_TITLE = "Customer not licensed";
// The rows that start from records of their own (qa/sheets/d1.py) and run after a row has failed.
const OWN_RECORDS = /\bD1-T1[56]\b/;

let password;
let users;
let sheet; // what the sheet starts from (qa/sheets/d1.py prepare_sheet)
const made = {}; // the records the rows make, for the rows after them

// What went wrong in the row: a refused or failed request, or an error message on screen, that
// the row did not expect. Every row ends by checking it is empty.
const run = { problems: [], expected: [] };

// --- Signing in -----------------------------------------------------------------------------

// Signs in without writing the password into the command log, leaving the previous person's
// page first, as a person signs out before the next signs in (standard_flow.js sign_in).
function sign_in(job) {
	const email = users[job];
	leave_page();
	cy.session(["sheet-d1", email], () => {
		cy.request({
			url: "/api/method/login",
			method: "POST",
			body: { usr: email, pwd: password },
			log: false,
		});
	});
}

// The Administrator who runs the tests, for set-up the sheet names as a precondition.
function sign_in_as_administrator() {
	leave_page();
	cy.login();
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", "Administrator");
}

function leave_page() {
	// A form with unsaved changes asks "Leave site?" in a native dialog; the person leaves.
	cy.window({ log: false }).then((win) => {
		const frm = win.cur_frm;
		if (frm?.beforeUnloadListener) {
			win.removeEventListener("beforeunload", frm.beforeUnloadListener, { capture: true });
		}
		win.location.replace("about:blank");
	});
	cy.location("href", { log: false }).should("eq", "about:blank");
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

// The next refused request whose message matches is the one the row is about.
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

// Waits until no request has been in flight for quiet_ms, the screen is not frozen, and the open
// form is not about to reload (standard_flow.js settle).
function settle(quiet_ms = QUIET_MS) {
	return cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) => {
		let since = null;
		return until(() => {
			if (
				win.frappe.request.ajax_count > 0 ||
				win.frappe.dom.freeze_count > 0 ||
				win.cur_frm?.doc?.__needs_refresh
			) {
				since = null;
				return false;
			}
			since = since ?? Date.now();
			return Date.now() - since >= quiet_ms;
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
	const title = plain(dialog.$wrapper.find(".modal-title").text());
	const message = plain(dialog.$wrapper.find(".msgprint").text());
	return {
		dialog,
		title,
		message,
		text: `${title}: ${message}`,
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

// The row went as expected: no error message on screen, no unexpected refusal, and every refusal
// the row expected has happened.
function no_problems(row) {
	settle();
	cy.window({ log: false }).then((win) => {
		const shown = message_dialog(win);
		if (shown?.error) run.problems.push(`the message "${shown.text}" is shown`);
		const problems = run.problems.splice(0);
		const missing = run.expected.splice(0);
		expect(problems, `${row}: problems\n${problems.join("\n")}\n`).to.be.empty;
		expect(missing.map(String), `${row}: refusals expected but not made`).to.be.empty;
	});
}

// The row's refusal is on screen as an error message saying `pattern`, under `title` when given;
// it is closed, as the person closes it.
function refused_with(pattern, title) {
	cy.window()
		.should((win) => {
			const shown = message_dialog(win);
			expect(shown, "an error message").to.not.equal(null);
			expect(shown.error, `"${shown.text}" is an error`).to.be.true;
			expect(shown.message).to.match(pattern);
			if (title) expect(shown.title, "the message's title").to.equal(title);
		})
		.then({ timeout: 10000 }, (win) =>
			close_message(win).then((closed) => expect(closed, "the message closes").to.be.true)
		);
}

// A message as a pattern of its exact words; an apostrophe may come HTML-escaped.
function words(text) {
	return String(text)
		.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
		.replace(/'/g, "(?:'|&#39;|&#x27;)");
}

function exactly(text) {
	return new RegExp(words(text));
}

// The licence check's refusal (customer_licence.require_licence) for a record made today.
function not_licensed(customer, record_type) {
	return cy
		.window()
		.then((win) =>
			exactly(
				`Customer ${customer} holds no licence verified by QA that is valid on ${today_text(
					win
				)}, so no ${record_type} is made for it. Sales records the customer's licence and QA verifies it.`
			)
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

function today_text(win) {
	return win.frappe.datetime.str_to_user(win.frappe.datetime.get_today());
}

function in_days(win, days) {
	return win.frappe.datetime.add_days(win.frappe.datetime.get_today(), days);
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

function inputs_of(container) {
	return `${container} input:visible, ${container} select:visible`;
}

// Types into a field's input and leaves it, as the person would.
function type_into($input, text) {
	cy.wrap($input).clear().type(text, { delay: 0 });
	// A date picker may already have taken the focus away.
	cy.document().then((doc) => {
		if (doc.activeElement === $input[0]) cy.wrap($input).blur();
	});
}

// A date is kept only once the person leaves its field ('change' on blur), and a form refresh set
// off by the field before (a fetch, a dependent field, ERPNext's own form script) can land in
// between and draw the box again from the still empty record: the person sees the box go empty
// and types the date again (found in CI, 2026-10-20, on a slow runner: the box empty in the
// screenshot). Typed again once only, and only when both the record and the box are empty: a box
// still showing a date the record did not take is a defect, left to the check that follows. The
// field is found again: the refresh drew it anew.
function retype_if_wiped(selector, value, label, read_stored, reopen = null) {
	settle();
	cy.window().then((win) => {
		const box = Cypress.$(win.document.body).find(selector).first().val();
		if (read_stored(win) || box) return;
		cy.log(`${label}: date wiped by a form refresh, typed again`);
		if (reopen) reopen();
		cy.get(selector)
			.first()
			.then(($input) => type_into($input, win.frappe.datetime.str_to_user(value)));
	});
}

// Types into a link field and chooses the entry of its list for `value`: the entry's name in
// bold, or, on a lookup that shows titles, its name under the title.
function choose($input, value) {
	cy.wrap($input).clear().focus().type(value, { delay: 0 });
	cy.get('[role="listbox"]:visible [role="option"]')
		.filter(
			(_, option) =>
				option.querySelector("strong")?.textContent.trim() === value ||
				option.textContent.includes(value)
		)
		.first()
		.click();
}

// Closes a lookup's list left open over the fields below it (standard_flow.js close_open_lists).
function lookup_fields_with_open_lists($body) {
	return $body
		.find('[role="listbox"]:visible')
		.toArray()
		.map((list) => $body.find(`input[aria-owns="${list.id}"]`))
		.filter(($field) => $field.length);
}

function close_open_lists() {
	cy.get("body", { log: false }).then(($body) => {
		for (const $field of lookup_fields_with_open_lists($body)) {
			$field[0].blur();
		}
	});
	cy.get("body", { log: false }).should(($body) =>
		expect(lookup_fields_with_open_lists($body), "lookup lists left open").to.have.length(0)
	);
}

// Fills a field once the form has finished updating from the last one (standard_flow.js fill).
function fill(fieldname, value, kind = "Data") {
	settle();
	close_open_lists();
	reveal(fieldname);
	cy.get(inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`))
		.first()
		.then(($input) =>
			cy.window().then((win) => {
				if (kind === "Link") choose($input, value);
				else if (kind === "Select") cy.wrap($input).select(value);
				else if (kind === "Date")
					type_into($input, win.frappe.datetime.str_to_user(value));
				else type_into($input, String(value));
			})
		);
	if (kind === "Date")
		retype_if_wiped(
			inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`),
			value,
			fieldname,
			(win) => current(win)[fieldname]
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
	settle();
	close_open_lists();
	const cell = `${FORM} .frappe-control[data-fieldname="${table}"] .grid-row[data-idx="${idx}"] [data-fieldname="${fieldname}"]`;
	cy.get(cell).first().click();
	// Typed once the clicked cell has the cursor (standard_flow.js fill_row).
	cy.get(inputs_of(cell)).first().should("have.focus");
	cy.get(inputs_of(cell))
		.first()
		.then(($input) => {
			if (kind === "Link") choose($input, value);
			else type_into($input, String(value));
		});
	cy.window().should((win) => {
		const stored = rows(win, table)[idx - 1][fieldname];
		if (typeof value === "number") expect(stored, fieldname).to.be.closeTo(value, 1e-6);
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

function primary_action() {
	return cy.get(`${FORM} .page-actions .primary-action:visible`);
}

// No Submit is offered on the form: none at all, or another action.
function no_submit_offered() {
	settle();
	cy.get(FORM).should(($form) =>
		expect($form.find(".page-actions .primary-action:visible").text()).not.to.contain("Submit")
	);
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

// Save is pressed and refused with `pattern`; the record is left as it was.
function save_refused(pattern, title) {
	expect_refusal(pattern);
	primary_action().should("contain", "Save").click();
	refused_with(pattern, title);
}

// Waits for the dialog that has just opened to finish opening, as a person would before using it.
function open_dialog() {
	cy.get(".modal.show").should("have.css", "opacity", "1");
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the dialog has finished opening").to.equal(true);
		expect(win.cur_dialog.$wrapper.is(".modal.show"), "it is the dialog shown").to.equal(true);
	});
	return cy.get(".modal.show").last();
}

function press_submit() {
	primary_action().should("contain", "Submit").click();
	open_dialog().should("contain", "Permanently Submit");
	cy.get(".modal.show .btn-primary").contains("Yes").click();
}

function submit() {
	press_submit();
	cy.window().should((win) => expect(current(win).docstatus, "submitted").to.equal(1));
	settle();
}

// Submit is pressed and refused with `pattern`; the record stays a draft.
function submit_refused(pattern, title) {
	expect_refusal(pattern);
	press_submit();
	refused_with(pattern, title);
	cy.window().should((win) => expect(current(win).docstatus, "still a draft").to.equal(0));
}

// The banner at the top of the form, in its colour.
function banner(colour, text) {
	return cy.get(`${FORM} .form-message.${colour}`).should("contain", text);
}

// The electronic signatures shown on the form (esign.js), first signed first.
function signature_rows() {
	return cy.get(`${FORM} .mds-signatures`).last().find("tbody tr");
}

// --- The signing dialog (compliance/esign.js) --------------------------------------------------

function open_signing(label) {
	button(label).click();
	cy.get(".modal.show").should("have.css", "opacity", "1");
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
		expect(win.cur_dialog.title).to.equal(`Sign: ${label}`);
	});
}

// Each value once the dialog has taken it, as a person fills it in.
function enter_reason(reason) {
	cy.get('.modal.show textarea[data-fieldname="reason"]').click().clear().type(reason);
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
}

function enter_password(value) {
	cy.get('.modal.show input[data-fieldname="password"]')
		.click()
		.clear()
		.type(value, { log: false });
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("password")).to.equal(value));
}

function press_sign() {
	cy.get(".modal:visible .btn-primary").contains("Sign").click();
}

// Signs `label` with a reason and the signer's password; the form then reloads the record.
function sign(label, reason) {
	open_signing(label);
	enter_reason(reason);
	enter_password(password);
	press_sign();
	cy.get(".modal.show").should("not.exist");
	settle(RELOAD_DELAY_MS + QUIET_MS);
}

function close_signing() {
	cy.get(".modal.show .btn-modal-close").click();
	cy.get(".modal.show").should("not.exist");
}

// --- Rows of the sheet ------------------------------------------------------------------------

// Sales records a licence of the sheet's customer from the customer's form: its type, number,
// issuing authority, validity (days from today) and copy, as the form asks for them.
function record_licence(number, from_days, until_days) {
	cy.then(() => open_form("Customer", made.customer));
	button("Record Licence").click();
	form_is("Customer Licence", (doc) => doc.__islocal && doc.customer === made.customer);
	settle();
	fill("licence_type", sheet.licence_type, "Link");
	fill("licence_number", number);
	fill("issuing_authority", "State Licensing Authority");
	cy.window().then((win) => fill("valid_from", in_days(win, from_days), "Date"));
	cy.window().then((win) => fill("valid_until", in_days(win, until_days), "Date"));
	// The copy, as the attach control uploads it.
	upload_private_file("licence-copy.pdf").then((url) =>
		cy.window().then((win) => win.cur_frm.set_value("licence_copy", url))
	);
	cy.window().should((win) => expect(current(win).licence_copy, "licence copy").to.be.ok);
}

// A private file uploaded by the signed-in user, as the attach control does; its address.
function upload_private_file(name) {
	return cy.fixture("controlled-document.pdf", null).then((pdf) =>
		cy.window({ log: false }).then((win) => {
			const form = new win.FormData();
			form.append("file", new win.Blob([pdf], { type: "application/pdf" }), name);
			form.append("is_private", "1");
			return win
				.fetch("/api/method/upload_file", {
					method: "POST",
					body: form,
					headers: { "X-Frappe-CSRF-Token": win.frappe.csrf_token },
				})
				.then((response) => response.json())
				.then((r) => r.message.file_url);
		})
	);
}

// Sales makes a sales order for the sheet's customer: the product, delivered in a week.
function new_sales_order(qty) {
	new_form("Sales Order");
	fill("customer", made.customer, "Link");
	cy.window().then((win) => fill("delivery_date", in_days(win, 7), "Date"));
	rows_up_to("items", 1);
	fill_row("items", 1, "item_code", sheet.product, "Link");
	fill_row("items", 1, "qty", qty);
	save();
}

// The record as stored, read as the signed-in user.
function stored(doctype, name) {
	return cy.call("frappe.client.get", { doctype, name }).then((r) => r.message);
}

// A server call the signed-in user makes that must be refused, as a script or another tool would
// send it past the form (sheet_d10.js refused_call): its status and message.
function refused_call(method, args) {
	// The desk's token, as the other sheets send it; a test site that does not check it has none.
	return cy
		.window()
		.then((win) => win.frappe?.csrf_token)
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
			expect(response.status, `${method} is refused`).to.be.within(400, 499);
			return { status: response.status, message: server_message(response.body) };
		});
}

// The form's menu (⋯) offers `label`, or does not; the menu is closed again when it is not.
function in_menu(label, expected) {
	settle();
	cy.get(`${FORM} .menu-btn-group > button`).click();
	cy.get(".dropdown-menu.show").should("be.visible");
	// By its label: an item with a keyboard shortcut (Delete) shows the shortcut beside it.
	const items = () =>
		cy
			.get(".dropdown-menu.show .dropdown-item")
			.filter(
				(_, item) =>
					plain((item.querySelector(".menu-item-label") ?? item).textContent) === label
			);
	if (expected) {
		items().should("have.length", 1).click();
		return;
	}
	items().should("have.length", 0);
	cy.get(`${FORM} .menu-btn-group > button`).click();
	cy.get(".dropdown-menu.show").should("not.exist");
}

// What a licence holds that a deletion or a cancellation would change, as stored.
const KEPT_FIELDS = ["docstatus", "status", "reason", "decided_by", "decided_at", "modified"];

function licence_as_stored(name) {
	return stored("Customer Licence", name).then((licence) =>
		Object.fromEntries(KEPT_FIELDS.map((field) => [field, licence[field]]))
	);
}

// An entry of the record's audit trail on its form (compliance/audit_timeline.py describe) that
// shows every one of `lines`, recorded today: its time is shown with it.
function audit_entry_shown(lines) {
	cy.window().then((win) =>
		cy.get(`${FORM} .timeline-item`).should(($items) => {
			const entries = $items
				.toArray()
				.filter((item) => lines.every((line) => plain(item.textContent).includes(line)));
			expect(
				entries,
				`an entry of the audit trail showing: ${lines.join(" | ")}`
			).to.have.length.at.least(1);
			const [entry] = entries;
			expect(entry.dataset.timestamp?.slice(0, 10), "recorded today").to.equal(
				win.frappe.datetime.get_today()
			);
			// "… ago", its date and time on hover, or the date and time where the site shows them so.
			const time = entry.querySelector(".frappe-timestamp");
			expect(
				time?.title.includes(today_text(win)) ||
					plain(entry.textContent).includes(today_text(win)),
				"its time shown"
			).to.be.true;
		})
	);
}

// The record's own audit trail, opened with the form's View > Audit Trail (public/js/audit_trail.js):
// the Audit Event list of that record only, showing each of `actions`.
function open_audit_trail(doctype, name, actions) {
	from_menu("View", "Audit Trail");
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a list is open").to.equal("List");
		expect(win.cur_list?.doctype).to.equal("Audit Event");
	});
	settle();
	cy.window().should((win) => {
		const filters = win.cur_list.filter_area.get().map((filter) => [filter[1], filter[3]]);
		expect(filters, "the list is of this record").to.deep.include.members([
			["reference_doctype", doctype],
			["reference_name", name],
		]);
	});
	cy.get(".page-container:visible .list-row-container:not(:has(.list-row-head))").should(
		($rows) => {
			const shown = $rows.toArray().map((row) => plain(row.textContent));
			for (const action of actions) {
				expect(
					shown.some((row) => row.includes(action)),
					`an entry "${action}" listed`
				).to.be.true;
			}
		}
	);
}

// Each row builds on the one before, so a row is never tried again (its first attempt may have
// done part of its work), and once one fails the rows after it are not run.
context("D1 Customer Master: the browser test sheet, as each job", { retries: 0 }, () => {
	let stopped = false;

	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		// The standard flow's product, approved, priced and in the finished goods store.
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password }));
		cy.then(() => cy.call(`${SHEET}.prepare_sheet`, { password })).then(({ message }) => {
			sheet = message;
		});
	});

	beforeEach(function () {
		if (stopped && !OWN_RECORDS.test(this.currentTest.title)) this.skip();
		run.problems.length = 0;
		run.expected.length = 0;
		watch_requests();
	});

	afterEach(function () {
		if (this.currentTest.state === "failed") stopped = true;
	});

	it("[URS-MST-008] D1-T01 Sales creates a customer and records its licence", () => {
		sign_in("Sales");
		new_form("Customer");
		fill("customer_name", sheet.customer_name);
		fill("customer_type", "Company", "Select");
		fill("customer_group", sheet.customer_group, "Link");
		save();
		cy.window().then((win) => {
			made.customer = current(win).name;
			expect(current(win).customer_name).to.equal(sheet.customer_name);
		});
		banner("red", NOT_LICENSED_BANNER);

		record_licence("TEST-LIC-001", -10, 365);
		cy.window().then((win) => {
			made.valid_until = current(win).valid_until;
		});
		save();
		cy.window().then((win) => {
			const year = win.frappe.datetime.get_today().slice(0, 4);
			expect(current(win).name).to.match(new RegExp(`^CL-${year}-\\d{5}$`));
			expect(current(win).docstatus).to.equal(0);
			made.licence = current(win).name;
		});
		banner("orange", AWAITING_BANNER);

		// Sales is offered no Submit, Verify or Reject, and QA's decision is not Sales' to fill.
		no_submit_offered();
		offered("Verify", false);
		offered("Reject", false);
		cy.window().then((win) => {
			for (const field of STATUS_FIELDS) {
				expect(current(win)[field], field).to.not.be.ok;
				expect(win.cur_frm.get_field(field).df.read_only, `${field} is read-only`).to.be
					.ok;
			}
		});
		cy.get(FORM).should(($form) => {
			for (const field of STATUS_FIELDS) {
				const $inputs = $form.find(
					`.frappe-control[data-fieldname="${field}"] :input:visible:enabled`
				);
				expect($inputs, `${field} cannot be typed into`).to.have.length(0);
			}
		});
		no_problems("D1-T01");
	});

	it("[URS-MST-008] D1-T02 A licence whose dates are out of order is refused", () => {
		sign_in("Sales");
		record_licence("TEST-LIC-001", 0, -1);
		save_refused(exactly("A licence is valid from a day up to a later one; check its dates."));
		cy.window().should((win) => expect(win.cur_frm.is_new(), "not saved").to.be.ok);
		no_problems("D1-T02");
	});

	it("[URS-MST-008] D1-T03 A sales order for a customer without a verified licence is refused on submit", () => {
		sign_in("Sales");
		new_sales_order(10);
		cy.window().then((win) => {
			made.order = current(win).name;
			expect(current(win).docstatus, "the draft saves").to.equal(0);
		});
		not_licensed(made.customer, "sales order").then((pattern) =>
			submit_refused(pattern, NOT_LICENSED_TITLE)
		);
		cy.then(() => stored("Sales Order", made.order)).then((order) =>
			expect(order.docstatus, "the order stays a draft").to.equal(0)
		);
		no_problems("D1-T03");
	});

	it("[URS-MST-008] D1-T04 Sales cannot verify a licence", () => {
		sign_in("Sales");
		cy.then(() => open_form("Customer Licence", made.licence));
		no_submit_offered();
		offered("Verify", false);
		offered("Reject", false);
		banner("orange", AWAITING_BANNER);
		no_problems("D1-T04");
	});

	it("[URS-MST-008] D1-T05 QA verifies the licence with an electronic signature", () => {
		const reason = "Copy checked against the authority's register";
		sign_in("QA Manager");
		cy.then(() => open_form("Customer Licence", made.licence));
		offered("Verify", true);
		offered("Reject", true);
		cy.get(`${FORM} .primary-action:visible`).should("not.exist");

		// Without a reason the dialog does not sign.
		open_signing("Verify");
		cy.get(".modal.show").should("contain", "Verified: I have verified this.");
		enter_password(password);
		press_sign();
		refused_with(/Following fields have missing values:\s*Reason/, "Missing Values Required");
		cy.window().should((win) => {
			expect(win.cur_dialog?.title, "the signing dialog stays open").to.equal(
				"Sign: Verify"
			);
			expect(current(win).docstatus, "not signed").to.equal(0);
		});

		// A wrong password signs nothing.
		const wrong = /Incorrect password\. The signature was not recorded\./;
		enter_reason(reason);
		enter_password(`${password}-wrong`);
		expect_refusal(wrong);
		press_sign();
		refused_with(wrong);
		cy.then(() => stored("Customer Licence", made.licence)).then((licence) =>
			expect(licence.docstatus, "the licence stays a draft").to.equal(0)
		);

		// The correct password signs.
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.title).to.equal("Sign: Verify"));
		enter_password(password);
		press_sign();
		cy.get(".modal.show").should("not.exist");
		settle(RELOAD_DELAY_MS + QUIET_MS);
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.docstatus, "verified").to.equal(1);
			expect(doc.status).to.equal("Verified");
			expect(doc.decided_by).to.equal(users["QA Manager"]);
			expect(doc.decided_at?.slice(0, 10)).to.equal(win.frappe.datetime.get_today());
		});
		banner("green", `Verified: ${reason}`);
		cy.window().then((win) =>
			signature_rows().should(($rows) => {
				expect($rows).to.have.length(1);
				expect($rows.eq(0).text())
					.to.contain("Verified")
					.and.to.contain(win.frappe.session.user_fullname)
					.and.to.contain(today_text(win))
					.and.to.contain(reason);
			})
		);

		cy.then(() => open_form("Customer", made.customer));
		cy.window().then((win) =>
			banner(
				"green",
				`Licence verified by QA: TEST-LIC-001 until ${win.frappe.datetime.str_to_user(
					made.valid_until
				)}.`
			)
		);
		no_problems("D1-T05");
	});

	it("[URS-MST-008] D1-T06 After verification the sales order submits", () => {
		sign_in("Sales");
		cy.then(() => open_form("Sales Order", made.order));
		submit();
		cy.window().should((win) => expect(current(win).status).to.equal("To Deliver and Bill"));
		no_problems("D1-T06");
	});

	it("[URS-MST-008] D1-T07 An expired licence cannot be verified", () => {
		// Precondition: Sales records a licence that expired 30 days ago.
		sign_in("Sales");
		record_licence("TEST-LIC-OLD", -400, -30);
		save();
		cy.window().then((win) => {
			made.old_licence = current(win).name;
			made.old_until = current(win).valid_until;
		});
		no_problems("D1-T07, Sales records the expired licence");

		sign_in("QA Manager");
		cy.then(() => open_form("Customer Licence", made.old_licence));
		open_signing("Verify");
		enter_reason("Copy checked (browser test)");
		enter_password(password);
		cy.window().then((win) => {
			const expired = exactly(
				`The licence expired on ${win.frappe.datetime.str_to_user(
					made.old_until
				)}; it cannot be verified.`
			);
			expect_refusal(expired);
			press_sign();
			refused_with(expired);
		});
		close_signing();
		cy.then(() => open_form("Customer Licence", made.old_licence));
		cy.window().should((win) => expect(current(win).docstatus, "still a draft").to.equal(0));
		cy.get(`${FORM} .mds-signatures`).should("not.exist");
		no_problems("D1-T07");
	});

	it("[URS-MST-008] D1-T08 QA rejects a licence", () => {
		const reason = "Expired copy supplied";
		sign_in("QA Manager");
		cy.then(() => open_form("Customer Licence", made.old_licence));
		sign("Reject", reason);
		cy.window().should((win) => {
			expect(current(win).docstatus).to.equal(1);
			expect(current(win).status).to.equal("Rejected");
		});
		banner("red", `Rejected: ${reason}`);
		signature_rows().should(($rows) => {
			expect($rows).to.have.length(1);
			expect($rows.eq(0).text()).to.contain("Rejected");
		});
		offered("Withdraw", false);
		no_problems("D1-T08");
	});

	it("[URS-MST-008] D1-T09 QA withdraws a verified licence and new sales are refused", () => {
		const reason = "Suspended by the authority";
		sign_in("QA Manager");
		cy.then(() => open_form("Customer Licence", made.licence));
		sign("Withdraw", reason);
		cy.window().should((win) => expect(current(win).status).to.equal("Withdrawn"));
		banner("red", `Withdrawn: ${reason}`);
		signature_rows().should(($rows) => {
			expect($rows).to.have.length(2);
			expect($rows.eq(1).text()).to.contain("Rejected");
		});

		cy.then(() => open_form("Customer", made.customer));
		banner("red", NOT_LICENSED_BANNER);
		no_problems("D1-T09, QA withdraws the licence");

		sign_in("Sales");
		new_sales_order(5);
		cy.window().then((win) => {
			made.second_order = current(win).name;
		});
		not_licensed(made.customer, "sales order").then((pattern) =>
			submit_refused(pattern, NOT_LICENSED_TITLE)
		);
		no_problems("D1-T09");
	});

	it("[URS-MST-008] D1-T10 A delivery note for an unlicensed customer is refused on submit", () => {
		sign_in("Stores");
		cy.then(() => open_form("Sales Order", made.order));
		from_menu("Create", "Delivery Note");
		form_is("Delivery Note", (doc) => doc.__islocal);
		settle();
		cy.window().should((win) =>
			current(win).items.forEach((row) => {
				expect(row.item_code).to.equal(sheet.product);
				expect(row.qty).to.equal(10);
				expect(row.warehouse).to.equal(sheet.finished_goods);
			})
		);
		save();
		not_licensed(made.customer, "delivery note").then((pattern) =>
			submit_refused(pattern, NOT_LICENSED_TITLE)
		);
		no_problems("D1-T10");
	});

	it("[URS-MST-008] D1-T11 QA exempts a customer from the licence and reverses it", () => {
		sign_in("QA Manager");
		cy.then(() => open_form("Customer", made.customer));
		banner("red", NOT_LICENSED_BANNER);
		sign("Exempt From Licence", "Hospital pharmacy buying for its own use");
		cy.window().should((win) => expect(current(win)[EXEMPT], "ticked").to.equal(1));
		banner("blue", EXEMPT_BANNER);
		signature_rows().should(($rows) => {
			expect($rows).to.have.length(1);
			expect($rows.eq(0).text()).to.contain("Approved");
		});
		offered("Require Licence", true);
		offered("Exempt From Licence", false);
		no_problems("D1-T11, QA exempts the customer");

		sign_in("Sales");
		cy.then(() => open_form("Sales Order", made.second_order));
		submit();
		no_problems("D1-T11, the order submits");

		sign_in("QA Manager");
		cy.then(() => open_form("Customer", made.customer));
		sign("Require Licence", "Now sells on to other parties: a licence is needed again");
		cy.window().should((win) => expect(current(win)[EXEMPT], "cleared").to.equal(0));
		banner("red", NOT_LICENSED_BANNER);
		signature_rows().should(($rows) => {
			expect($rows).to.have.length(2);
			expect($rows.eq(1).text()).to.contain("Rejected");
		});
		no_problems("D1-T11");
	});

	it("[URS-MST-008] D1-T12 Sales cannot change the exemption", () => {
		sign_in("Sales");
		cy.then(() => open_form("Customer", sheet.exempt_customer));
		banner("blue", EXEMPT_BANNER);
		cy.window().should((win) => {
			expect(current(win)[EXEMPT], "ticked").to.equal(1);
			expect(win.cur_frm.get_field(EXEMPT).df.read_only, "read-only").to.be.ok;
		});
		cy.get(FORM).should(($form) =>
			expect(
				$form.find(`.frappe-control[data-fieldname="${EXEMPT}"] input:visible:enabled`),
				"the tick box cannot be changed"
			).to.have.length(0)
		);
		offered("Exempt From Licence", false);
		offered("Require Licence", false);
		no_problems("D1-T12");
	});

	it("[URS-MST-008] [URS-MST-017] D1-T13 A customer with a decided licence is not renamed or merged", () => {
		const customer = () => sheet.licensed_customer;
		sign_in("Sales");
		cy.then(() => open_form("Customer", customer()));
		cy.get(`${FORM} .menu-btn-group > button`).click();
		// Frappe draws the menu outside its button group: the visible menu's item, by its text.
		cy.get(".dropdown-menu.show")
			.contains(".dropdown-item", /^\s*Rename\s*$/)
			.click();
		open_dialog().should("contain", "New Name");
		cy.then(() => {
			const renamed = `${customer()} 2`;
			cy.get('.modal.show input[data-fieldname="new_name"]').clear().type(renamed);
			cy.window()
				.its("cur_dialog")
				.should((dialog) => expect(dialog.get_value("new_name")).to.equal(renamed));
			const refusal = exactly(
				`${customer()} holds a licence or an exemption QA has decided, so it is not renamed. Record a new customer and its licence instead.`
			);
			expect_refusal(refusal);
			cy.get(".modal.show .btn-primary").contains("Rename").click();
			refused_with(refusal);
			cy.call("frappe.client.get_count", {
				doctype: "Customer",
				filters: { name: ["in", [customer(), renamed]] },
			}).then(({ message }) => expect(message, "the customer keeps its name").to.equal(1));
			cy.call("frappe.client.get_count", {
				doctype: "Customer",
				filters: { name: customer() },
			}).then(({ message }) => expect(message, "under its old name").to.equal(1));
		});

		// Nor merged into another customer, decided or not (URS-MST-017): its licence would move to
		// a customer QA never verified it for.
		cy.then(() => open_form("Customer", customer()));
		settle();
		cy.get(`${FORM} .menu-btn-group > button`).click();
		cy.get(".dropdown-menu.show")
			.contains(".dropdown-item", /^\s*Rename\s*$/)
			.click();
		open_dialog().should("contain", "New Name");
		cy.then(() => {
			const other = sheet.exempt_customer;
			cy.get('.modal.show input[data-fieldname="new_name"]').clear().type(other);
			cy.get('.modal.show input[data-fieldname="merge"]').check();
			cy.window()
				.its("cur_dialog")
				.should((dialog) => {
					expect(dialog.get_value("new_name")).to.equal(other);
					expect(dialog.get_value("merge")).to.equal(1);
				});
			const refusal = exactly(
				`Customer ${customer()} is not merged into ${other}: approvals and history stay with the record they were made for. Disable the duplicate instead.`
			);
			expect_refusal(refusal);
			cy.get(".modal.show .btn-primary").contains("Rename").click();
			refused_with(refusal);
			cy.call("frappe.client.get_count", {
				doctype: "Customer",
				filters: { name: ["in", [customer(), other]] },
			}).then(({ message }) => expect(message, "both customers kept").to.equal(2));
			cy.call("frappe.client.get_count", {
				doctype: "Customer Licence",
				filters: { customer: customer(), docstatus: 1 },
			}).then(({ message }) =>
				expect(message, "its verified licence stays with it").to.be.at.least(1)
			);
		});
		no_problems("D1-T13");
	});

	it("[URS-MST-001] D1-T14 A customer in use changes only under an approved change request", () => {
		sign_in("Sales");
		cy.then(() => open_form("Customer", made.customer));
		reveal("mds_version");
		cy.window().then((win) => {
			made.version = current(win).mds_version;
			expect(made.version, "a version").to.be.at.least(1);
		});

		// A controlled field, without a change request: refused.
		fill("customer_group", sheet.other_group, "Link");
		cy.then(() =>
			save_refused(
				exactly(
					`Customer ${made.customer} is in use: name the approved change request that lists it to change it.`
				),
				"Change control"
			)
		);

		// Undone, and only a contact detail changed: saved, the version as it was.
		fill("customer_group", sheet.customer_group, "Link");
		fill("website", "https://test-d1-medical-stores.example.com");
		save();
		cy.window().should((win) =>
			expect(current(win).mds_version, "version unchanged").to.equal(made.version)
		);
		no_problems("D1-T14, an uncontrolled change");

		// Precondition: a change request listing the customer, raised by Sales and approved by QA.
		sign_in_as_administrator();
		cy.then(() =>
			cy.call(`${SHEET}.approve_customer_change`, { customer: made.customer, password })
		).then(({ message }) => {
			made.change_request = message;
		});

		sign_in("Sales");
		cy.then(() => open_form("Customer", made.customer));
		fill("customer_group", sheet.other_group, "Link");
		cy.then(() => fill("mds_change_request", made.change_request, "Link"));
		save();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.customer_group).to.equal(sheet.other_group);
			expect(doc.mds_version, "version raised by 1").to.equal(made.version + 1);
			expect(doc.mds_effective_from).to.equal(win.frappe.datetime.get_today());
			expect(doc.mds_changed_under).to.equal(made.change_request);
			expect(doc.mds_change_request, "named afresh for each change").to.not.be.ok;
		});
		no_problems("D1-T14");
	});

	// 21 CFR 11.10(e): QA reads who changed a customer in use, when, from what to what and under
	// which change request, and who verified its licence, on the forms and in the audit trail.
	it("[URS-MST-008] [URS-CMP-012] D1-T15 QA reads the audit trail of a customer and its licence", () => {
		const customer = () => sheet.audited_customer;
		const licence = () => sheet.audited_licence;
		const change = () => sheet.audited_change_request;
		const sales = () => sheet.full_names.Sales;
		const qa = () => sheet.full_names["QA Manager"];
		const audit = {};
		sign_in("QA Manager");

		// On the customer's form: who changed its group, when, the old and new value, and the
		// approved change request it was changed under.
		cy.then(() => open_form("Customer", customer()));
		cy.window().should((win) => {
			expect(current(win).customer_group).to.equal(sheet.other_group);
			expect(current(win).mds_changed_under).to.equal(change());
		});
		cy.then(() =>
			audit_entry_shown([
				`Updated by ${sales()}`,
				`Customer Group: ${sheet.customer_group} → ${sheet.other_group}`,
				`Last Changed Under: empty → ${change()}`,
			])
		);

		// Its whole audit trail, from the form's View menu, and the entry of the change opened.
		cy.then(() => open_audit_trail("Customer", customer(), ["Created", "Updated"]));
		cy.then(() =>
			cy.call("frappe.client.get_list", {
				doctype: "Audit Event",
				filters: {
					reference_doctype: "Customer",
					reference_name: customer(),
					action: "Updated",
				},
				fields: ["name", "changes"],
				limit_page_length: 0,
			})
		).then(({ message }) => {
			const of_the_change = message.filter((event) =>
				(typeof event.changes === "string"
					? event.changes
					: JSON.stringify(event.changes)
				).includes(change())
			);
			expect(of_the_change, "one entry records the change").to.have.length(1);
			audit.event = of_the_change[0].name;
		});
		cy.then(() =>
			cy.get(`.page-container:visible .list-row a[data-name="${audit.event}"]`).click()
		);
		cy.then(() => form_is("Audit Event", (doc) => doc.name === audit.event));
		settle();
		cy.window().should((win) => {
			const doc = current(win);
			expect([doc.reference_doctype, doc.reference_name]).to.deep.equal([
				"Customer",
				customer(),
			]);
			expect(doc.action).to.equal("Updated");
			expect(doc.user, "who changed it").to.equal(users.Sales);
			expect(doc.event_at.slice(0, 10), "when").to.equal(win.frappe.datetime.get_today());
		});
		cy.get(`${FORM} .frappe-control[data-fieldname="action"]`).should("contain", "Updated");
		cy.get(`${FORM} .frappe-control[data-fieldname="user"]`).should(
			($control) =>
				expect(
					[users.Sales, sales()].some((who) => $control.text().includes(who)),
					"who changed it, shown"
				).to.be.true
		);
		cy.window().then((win) =>
			cy
				.get(`${FORM} .frappe-control[data-fieldname="event_at"]`)
				.should("contain", today_text(win))
		);
		reveal("changes");
		cy.get(`${FORM} .frappe-control[data-fieldname="changes"]`).should(($control) => {
			const text = $control.text();
			for (const value of [
				"customer_group",
				sheet.customer_group,
				sheet.other_group,
				"mds_changed_under",
				change(),
			]) {
				expect(text, "the old and new values, as stored").to.contain(value);
			}
		});

		// Why: the approved change request the customer was changed under.
		cy.then(() => open_form("Change Request", change()));
		cy.window().should((win) => {
			expect(current(win).status).to.equal("Approved");
			expect(current(win).reason).to.equal(sheet.change_reason);
		});
		reveal("reason");
		cy.get(`${FORM} .frappe-control[data-fieldname="reason"]`).should(
			"contain",
			sheet.change_reason
		);

		// The customer's licence: QA's Verified signature, who signed, when and why, and the
		// verification in its audit trail.
		cy.then(() => open_form("Customer Licence", licence()));
		banner("green", `Verified: ${sheet.licence_checked}`);
		cy.window().then((win) =>
			signature_rows().should(($rows) => {
				expect($rows).to.have.length(1);
				expect($rows.eq(0).text())
					.to.contain("Verified")
					.and.to.contain(qa())
					.and.to.contain(today_text(win))
					.and.to.contain(sheet.licence_checked);
			})
		);
		cy.then(() => audit_entry_shown([`Submitted by ${qa()}`, "Status: empty → Verified"]));
		cy.then(() => open_audit_trail("Customer Licence", licence(), ["Created", "Submitted"]));
		no_problems("D1-T15");
	});

	// ISO 13485 4.2.5, 21 CFR 11.10(c): a licence is kept as decided. QA withdraws one; nobody, the
	// Administrator included, deletes or cancels it, on the form or past it.
	it("[URS-MST-008] [URS-CMP-016] D1-T16 A customer licence is never deleted or cancelled, not even by the Administrator", () => {
		const verified = () => sheet.kept_licence;
		const awaiting = () => sheet.awaiting_licence;
		const never_deleted = exactly("A customer licence is never deleted.");
		const never_cancelled = exactly(
			"A customer licence is never cancelled; QA withdraws it, and a renewed licence is a new record."
		);
		// Frappe refuses to delete a submitted record before the licence's own rule is reached.
		const submitted_not_deleted = /Submitted Record cannot be deleted/;
		const before = {};
		const target = (name) => ({ doctype: "Customer Licence", name });

		// QA, who decides licences: the form offers neither, and a call past the form is refused.
		sign_in("QA Manager");
		cy.visit("/desk"); // the server calls below take their token from a desk page
		cy.then(() => licence_as_stored(verified())).then((kept) => {
			expect([kept.docstatus, kept.status], "verified").to.deep.equal([1, "Verified"]);
			before.verified = kept;
		});
		cy.then(() => licence_as_stored(awaiting())).then((kept) => {
			expect(kept.docstatus, "awaiting QA").to.equal(0);
			before.awaiting = kept;
		});
		cy.then(() => open_form("Customer Licence", verified()));
		banner("green", `Verified: ${sheet.licence_checked}`);
		offered("Cancel", false);
		in_menu("Delete", false);
		for (const method of ["frappe.client.delete", "frappe.client.cancel"]) {
			cy.then(() => refused_call(method, target(verified()))).then(({ status, message }) => {
				expect(status, `${method} as QA`).to.equal(403);
				expect(message).to.match(/PermissionError/);
			});
		}
		cy.then(() => open_form("Customer Licence", awaiting()));
		in_menu("Delete", false);
		cy.then(() => refused_call("frappe.client.delete", target(awaiting()))).then(
			({ status, message }) => {
				expect(status, "deleting the licence awaiting QA, as QA").to.equal(403);
				expect(message).to.match(/PermissionError/);
			}
		);
		no_problems("D1-T16, QA");

		// The Administrator, who holds every permission.
		sign_in_as_administrator();
		cy.then(() => open_form("Customer Licence", verified()));
		// No Delete on a submitted record; a call past the form is refused.
		in_menu("Delete", false);
		cy.then(() => refused_call("frappe.client.delete", target(verified()))).then(
			({ message }) => expect(message).to.match(submitted_not_deleted)
		);
		// No role holds cancel on a licence, the System Manager included: the form offers no Cancel,
		// not even to the Administrator, and a call past the form is refused by the licence's rule.
		offered("Cancel", false);
		cy.then(() => refused_call("frappe.client.cancel", target(verified()))).then(
			({ message }) => expect(message).to.match(never_cancelled)
		);
		// The licence awaiting QA: Delete is offered, and refused.
		cy.then(() => open_form("Customer Licence", awaiting()));
		in_menu("Delete", true);
		open_dialog().should("contain", "Permanently delete");
		expect_refusal(never_deleted);
		cy.get(".modal.show .btn-primary").contains("Yes").click();
		refused_with(never_deleted);
		cy.then(() => refused_call("frappe.client.delete", target(awaiting()))).then(
			({ message }) => expect(message).to.match(never_deleted)
		);

		// Both licences are still there, as they were.
		cy.then(() => licence_as_stored(verified())).then((kept) =>
			expect(kept, "the verified licence, unchanged").to.deep.equal(before.verified)
		);
		cy.then(() => licence_as_stored(awaiting())).then((kept) =>
			expect(kept, "the licence awaiting QA, unchanged").to.deep.equal(before.awaiting)
		);
		no_problems("D1-T16");
	});
});

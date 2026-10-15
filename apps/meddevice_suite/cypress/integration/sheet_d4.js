// Browser test sheet of data store D4, Inventory & Batch Ledger (modules 7, 16, 17, 18): stores and
// their roles, lots and lot status, goods receipt, certificate verification, incoming inspection,
// QA's Lot Release (release, reject, hold; return to stock and reject return for goods a customer
// sent back), expiry and first expiry, first out, a lot's dates fixed once it has stock with QA's
// signed Correct Dates, and finished goods only from production or opening stock
// (meddevice_suite/lot_control/, master_data/warehouse.py). One test per line of the sheet, in its
// order, each signed in as the job the sheet names and done on the screens as that person would.
// Refusals are checked word for word; a refusal only an API call can reach is posted as that job.
//
// The sheet names pilot records; the test site has none, so the run uses the journey's
// (meddevice_suite/qa/ui_journey.py) and records of its own (meddevice_suite/qa/sheets/d4.py):
//   RM-SILICONE -> UIJ-RM-SILICONE, FC-2W-SIL-16 -> UIJ-FC-2W-SIL-16, Silicone Compounds (Demo) and
//   City Hospital (Demo) -> the journey's supplier and customer, "- DMD" stores -> the test
//   company's, Digital Caliper (Demo) -> the journey's calibrated instrument;
//   the lots of D4-T13 are of UIJ-RM-CONNECTOR, whose earlier stock is written off first, and the
//   lot D4-T12 makes expired is of the sheet's own material UIJ-D4-RM-POUCH, never one the next
//   run's write-off has to scrap;
//   a raw material is not a sales item, so the goods a customer sends back (D4-T09, T10, T26) and
//   the delivery and pick list of D4-T11 are of the finished good, whose approved store is
//   Finished Goods.
// Each test starts from records d4.py makes for it through the code's own services, each step as
// the job that does it, so the tests stand on their own where the sheet builds one on another.

const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const SHEET = "meddevice_suite.qa.sheets.d4";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const LINE = ".grid-row-open"; // a line of a table, opened in its own form
const TIMEOUT = 20000; // the longest a page or an action may take
const QUIET_MS = 300; // no request in flight for this long: the page has finished
const RELOAD_DELAY_MS = 1000; // the desk reloads a form this long after the server changes it

const QA = "QA Manager";
const QC = "QC Inspector";
const STORES = "Stores";
const PURCHASE = "Purchase";
const MANAGEMENT = "Management";
const PRODUCTION = "Production Supervisor";
const SYSTEM_ADMINISTRATOR = "System Administrator";

let password;
let users;
let journey; // the journey's masters (ui_journey.prepare_journey)
let data; // what the sheet works with (qa/sheets/d4.py prepare_sheet)

// What went wrong in the test: a refused or failed request, or an error message on screen, that
// the test did not expect. Every test ends by checking it is empty.
const run = { problems: [], expected: [] };

// --- Signing in -----------------------------------------------------------------------------

// Signs in as the job without writing the password into the command log, after leaving the
// previous person's page, as a person signs out before the next signs in (standard_flow.js).
function sign_in(job) {
	const email = users[job];
	leave_page();
	signed_in_as(email, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", email);
}

// The Administrator who runs the tests prepares what a test starts from (qa/sheets/d4.py).
function prepare(method, args = {}) {
	as_administrator();
	return cy.call(`${SHEET}.${method}`, args).then((r) => r.message);
}

// A session kept between tests is checked before it is used: one the server has since ended
// (found in Chrome, 2026-10-15: the Administrator's, late in the run) is signed in again.
function signed_in_as(user, pwd) {
	cy.session(
		["sheet-d4", user],
		() => {
			cy.request({
				url: "/api/method/login",
				method: "POST",
				body: { usr: user, pwd },
				log: false,
			});
		},
		{
			validate() {
				cy.request({ url: "/api/method/frappe.auth.get_logged_user", log: false })
					.its("body.message")
					.should("eq", user);
			},
		}
	);
}

function as_administrator() {
	leave_page();
	signed_in_as("Administrator", Cypress.env("adminPassword"));
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", "Administrator");
}

function leave_page() {
	forget_unsaved();
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
}

// A form with unsaved changes asks "Leave site?" in a native dialog, which a person answers and
// which would freeze the test; the person leaves a refused form as it is, so the test does too.
function forget_unsaved() {
	cy.window({ log: false }).then((win) => {
		const forms = Object.values(win.frappe?.views?.formview || {}).map((view) => view.frm);
		for (const frm of [win.cur_frm, ...forms]) {
			if (frm?.beforeUnloadListener) {
				win.removeEventListener("beforeunload", frm.beforeUnloadListener, {
					capture: true,
				});
			}
		}
	});
}

// --- Watching for problems ------------------------------------------------------------------

function plain(html) {
	return String(html ?? "")
		.replace(/<[^>]*>/g, " ")
		.replace(/&#39;|&#x27;/g, "'")
		.replace(/&quot;/g, '"')
		.replace(/&lt;/g, "<")
		.replace(/&gt;/g, ">")
		.replace(/&amp;/g, "&")
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

// A pattern that matches `text` word for word.
function exact(text) {
	return new RegExp(String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
}

// The refusal the sheet expects: its message, and on screen its title before it.
function refusal(message, title = null) {
	return {
		server: exact(message),
		screen: title ? exact(`${title}: ${message}`) : exact(message),
	};
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

// Waits until no request has been in flight for quiet_ms, the screen is not frozen, and the open
// form is not about to reload (standard_flow.js). A line opened in its own form darkens the page
// behind it, which Frappe counts as one freeze; that one is the person's open line, not a wait.
function settle(quiet_ms = QUIET_MS) {
	return cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) => {
		let since = null;
		const frozen = () => win.frappe.dom.freeze_count > (win.cur_frm?.cur_grid ? 1 : 0);
		return until(() => {
			if (
				win.frappe.request.ajax_count > 0 ||
				frozen() ||
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

// The test went as expected: no error message on screen, no unexpected refusal, and every
// refusal it expected has happened.
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
			expect(response.status, `${method} is refused`).to.be.within(400, 499);
			return server_message(response.body);
		});
}

// --- Using the desk -------------------------------------------------------------------------

function slug(doctype) {
	return doctype.toLowerCase().replace(/ /g, "-");
}

function open_form(doctype, name) {
	forget_unsaved();
	cy.visit(`/desk/${slug(doctype)}/${encodeURIComponent(name)}`);
	form_is(doctype, (doc) => doc.name === name);
	settle();
}

function new_form(doctype) {
	forget_unsaved();
	cy.visit(`/desk/${slug(doctype)}/new`);
	form_is(doctype, (doc) => doc.__islocal);
	settle();
}

function open_list(doctype, filters = {}) {
	forget_unsaved();
	const query = new URLSearchParams(filters).toString();
	// The list view itself, also for a record type that opens as a tree by default (Warehouse).
	cy.visit(`/desk/${slug(doctype)}/view/list${query ? `?${query}` : ""}`);
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a list is open").to.equal("List");
		expect(win.cur_list?.doctype, "the list").to.equal(doctype);
	});
	settle();
}

// Opens a report with its filters set, as its menu link with the filters does; resolves with the
// lines it shows.
function open_report(name, filters = {}) {
	forget_unsaved();
	const query = new URLSearchParams(filters).toString();
	cy.visit(`/desk/query-report/${encodeURIComponent(name)}${query ? `?${query}` : ""}`);
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a report is open").to.equal("query-report");
		expect(win.frappe.query_report?.report_name, "the report").to.equal(name);
	});
	settle();
	cy.window().should((win) => {
		for (const [field, value] of Object.entries(filters)) {
			expect(win.frappe.query_report.get_filter_value(field), field).to.equal(value);
		}
		expect(win.frappe.query_report.data, "the report's lines").to.be.an("array");
	});
	settle();
	return cy.window().then((win) => win.frappe.query_report.data);
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
	return `${container} input:visible, ${container} select:visible, ${container} textarea:visible`;
}

function user_date(win, value) {
	return win.frappe.datetime.str_to_user(value);
}

function in_days(win, days) {
	return win.frappe.datetime.add_days(win.frappe.datetime.get_today(), days);
}

// The day `days` after an ISO date, as an ISO date.
function shifted(iso, days) {
	const day = new Date(`${iso}T00:00:00Z`);
	day.setUTCDate(day.getUTCDate() + days);
	return day.toISOString().slice(0, 10);
}

// Empties the field and types `value`, finding the field again for each step: clearing it can make
// the form redraw it (found in Chrome, 2026-10-15, on the work order's stores).
function type_afresh(selector, value) {
	settle();
	// In a line's form a field can sit below the fold: brought into view first, as a person scrolls
	// to it (found in Chrome, 2026-10-15, on the receipt line's supplier lot number). Every part of
	// the selector counts: the field may be a text area or a choice, not an input (D4-T13, D4-T16).
	cy.get(
		selector
			.split(",")
			.map((part) => part.trim().replace(/:visible(:first)?/g, ""))
			.join(", ")
	)
		.first()
		.scrollIntoView();
	cy.get(first_of(selector)).clear();
	settle();
	cy.get(first_of(selector)).type(value, { delay: 0 });
}

// Types into a link field, found by `selector`, and chooses the matching entry of its list.
function choose(selector, value, reopen = null) {
	type_afresh(selector, value);
	settle();
	// A form redrawn by what an earlier field brought (a supplier's details, an item, its bill) can
	// take the typing away with the old input, or close a table cell again; the person opens the
	// field again as it now stands and types it once more.
	cy.get("body").then(($body) => {
		const $input = $body.find(selector).first();
		const listed = $body.find('[role="listbox"]:visible [role="option"]').length > 0;
		if (!listed || !$input.length || $input.val() !== value) {
			if (reopen) reopen();
			type_afresh(selector, value);
		}
	});
	// The open list of the field: only one is open at a time. An entry shows the record's title in
	// bold with its name right after it, with no space; the entry named `value` is the one.
	// The list is chosen from once the lookup's search has come back and it stops changing; the entry
	// is looked up again on every try, as Frappe redraws the list when the search answers.
	settle();
	lookup_list_settled();
	cy.get('[role="listbox"]:visible [role="option"]')
		.filter(
			(_, option) =>
				option ===
				entry_named(
					Cypress.$(option).closest('[role="listbox"]').find('[role="option"]'),
					value
				)
		)
		.should("have.length", 1)
		.click();
}

// Waits until the open lookup list shows entries and their number has not changed for a while: the
// list is redrawn as each search answers, and an entry clicked meanwhile is gone (found in CI).
function lookup_list_settled() {
	cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) => {
		let last = -1;
		let since = Date.now();
		return until(() => {
			const count = win.$('[role="listbox"]:visible [role="option"]').length;
			if (count !== last) {
				last = count;
				since = Date.now();
				return false;
			}
			return count > 0 && Date.now() - since >= QUIET_MS;
		}).then((settled) => expect(settled, "the lookup's list has settled").to.be.true);
	});
}

function entry_named($options, value) {
	const options = $options.toArray();
	const squeezed = (text) => String(text ?? "").replace(/\s+/g, "");
	return (
		options.find((option) => option.querySelector("strong")?.textContent.trim() === value) ||
		options.find((option) => squeezed(option.textContent).endsWith(squeezed(value))) ||
		options.find((option) => squeezed(option.textContent).includes(squeezed(value)))
	);
}

// A lookup's list can open again after a choice and lie over the fields below it; a person
// closes it before going on (standard_flow.js).
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

// Puts `value` into an input as a person does for a field of `kind`: a lookup chosen from its
// list, a lot number typed in full as read off its label (a lookup of lots lists only those it
// thinks can be taken, and the sheet names others on purpose), a choice, a tick, a date, a text.
// Each action finds the field again by `selector`: the form may draw it anew in between.
function enter($input, value, kind, selector, reopen = null) {
	cy.window().then((win) => {
		if (kind === "Link") choose(selector, value, reopen);
		else if (kind === "Select") cy.get(first_of(selector)).select(value);
		else if (kind === "Check") cy.get(first_of(selector))[value ? "check" : "uncheck"]();
		else if (kind === "Date") type_and_leave(selector, user_date(win, value));
		else type_and_leave(selector, String(value));
	});
}

// The first match of each part of a selector list, as one query Cypress can run again when the
// page redraws (a chained .first() cannot be run again).
function first_of(selector) {
	return selector
		.split(",")
		.map((part) => `${part.trim()}:first`)
		.join(", ");
}

// Types into a field and leaves it, as the person would.
function type_and_leave(selector, text) {
	type_afresh(selector, text);
	// A date picker may already have taken the focus away.
	cy.document().then((doc) => {
		const active = doc.activeElement;
		if (active && Cypress.$(active).is(selector)) active.blur();
	});
}

function stored_as(stored, value, label) {
	if (typeof value === "number") expect(stored, label).to.be.closeTo(value, 1e-6);
	else expect(stored, label).to.equal(value);
}

// A person types the next field once the form has finished updating from the last one.
function fill(fieldname, value, kind = "Data") {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(fieldname);
	const selector = inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`);
	cy.get(selector)
		.first()
		.then(($input) => enter($input, value, kind, selector));
	cy.window().should((win) => stored_as(current(win)[fieldname], value, fieldname));
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

function cell(table, idx, fieldname) {
	return `${FORM} .frappe-control[data-fieldname="${table}"] .grid-row[data-idx="${idx}"] [data-fieldname="${fieldname}"]`;
}

function fill_row(table, idx, fieldname, value, kind = "Data") {
	settle();
	close_dialogs(); // and the Getting Started panel
	close_open_lists();
	cy.get(`${cell(table, idx, fieldname)}:first`).click();
	// Typed once the clicked cell has the cursor (standard_flow.js).
	cy.get(inputs_of(cell(table, idx, fieldname)))
		.first()
		.should("have.focus");
	cy.get(inputs_of(cell(table, idx, fieldname)))
		.first()
		.then(($input) =>
			enter($input, value, kind, inputs_of(cell(table, idx, fieldname)), () =>
				cy.get(`${cell(table, idx, fieldname)}:first`).click()
			)
		);
	cy.window().should((win) =>
		stored_as(rows(win, table)[idx - 1][fieldname], value, `line ${idx} ${fieldname}`)
	);
}

// Empties a cell, as a person deletes what it holds.
function clear_row(table, idx, fieldname) {
	settle();
	close_dialogs();
	close_open_lists();
	cy.get(`${cell(table, idx, fieldname)}:first`).click();
	cy.get(first_of(inputs_of(cell(table, idx, fieldname)))).should("have.focus");
	cy.get(first_of(inputs_of(cell(table, idx, fieldname)))).clear();
	cy.get(first_of(inputs_of(cell(table, idx, fieldname)))).blur();
	cy.window().should(
		(win) =>
			expect(rows(win, table)[idx - 1][fieldname], `line ${idx} ${fieldname}`).to.not.be.ok
	);
}

// Choosing a batch item on a line can open ERPNext's batch picker over the form; the person closes
// it with its cross and names the lot, if any, in the line's own form.
function dismiss_batch_picker() {
	settle();
	cy.window().then({ timeout: 10000 }, (win) => {
		const picker = () =>
			win.cur_dialog?.display && /Batch|Serial/.test(win.cur_dialog.title || "")
				? win.cur_dialog
				: null;
		// The picker opens once ERPNext has the item's details, which can come a moment late.
		return until(picker, 3000).then((open) => {
			if (open) close_dialogs();
		});
	});
	settle();
}

function fill_item(table, idx, item) {
	fill_row(table, idx, "item_code", item, "Link");
	dismiss_batch_picker();
}

// A value of the open line entered as data on the line, then checked: for the line fields Cypress
// cannot reach in the line's form (a folded section, its fixed layer; found in Chrome, 2026-10-15).
function set_line(fieldname, value) {
	settle();
	cy.window().then((win) => {
		const line = win.cur_frm.cur_grid.doc;
		return win.frappe.model.set_value(line.doctype, line.name, fieldname, value);
	});
	settle();
	cy.window().should((win) =>
		expect(win.cur_frm.cur_grid.doc[fieldname], fieldname).to.equal(value)
	);
}

// Opens a line of a table in its own form, where the fields the table does not show are.
function open_line(table, idx) {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(table); // the table's tab, if another tab is open
	close_onboarding();
	cy.get(
		`${FORM} .frappe-control[data-fieldname="${table}"] .grid-row[data-idx="${idx}"] .row-index:first`
	).click();
	cy.get(LINE).should("be.visible");
	cy.window().should((win) => {
		expect(win.cur_frm.cur_grid?.doc?.parentfield, "the table").to.equal(table);
		expect(win.cur_frm.cur_grid.doc.idx, "the line").to.equal(idx);
	});
}

function fill_line(fieldname, value, kind = "Data") {
	settle();
	close_dialogs();
	close_open_lists();
	// A line's own form is longer than the window; the person scrolls to the field first. A field
	// Cypress cannot reach there (in a folded section, or under the line's fixed layer: found in
	// Chrome, 2026-10-15) is entered as data on the line; the refusals that follow are the test.
	const control = `${LINE} .frappe-control[data-fieldname="${fieldname}"]`;
	const selector = inputs_of(control);
	cy.get(control)
		.first()
		.then(($control) => {
			$control[0].scrollIntoView({ block: "center" });
			const $input = $control
				.find("input, select, textarea")
				.filter((_, element) => Cypress.dom.isVisible(element));
			if ($input.length) {
				enter($input.first(), value, kind, selector);
			} else {
				cy.window().then((win) => {
					const line = win.cur_frm.cur_grid.doc;
					return win.frappe.model.set_value(line.doctype, line.name, fieldname, value);
				});
			}
		});
	cy.window().should((win) => stored_as(win.cur_frm.cur_grid.doc[fieldname], value, fieldname));
}

// A tick box of the open line, ticked as data entry: the line's form is drawn in a fixed layer
// whose tick boxes Cypress cannot reach (found in Chrome, 2026-10-15); the tick is not what the
// sheet tests, the refusals that follow are.
function tick_line(fieldname) {
	settle();
	cy.window().then((win) => {
		const line = win.cur_frm.cur_grid.doc;
		return win.frappe.model.set_value(line.doctype, line.name, fieldname, 1);
	});
	settle();
	cy.window().should((win) =>
		expect(win.cur_frm.cur_grid.doc[fieldname], fieldname).to.equal(1)
	);
}

function close_line() {
	settle();
	close_open_lists();
	// Closed as Frappe closes it: its collapse button may lie scrolled out of view, and the middle
	// of its heading can land on Delete.
	cy.window().then((win) => win.cur_frm.cur_grid?.toggle_view(false));
	cy.get(LINE).should("not.exist");
	settle();
}

// The lot of a line, named in the line's own form with Use Serial No / Batch Fields ticked.
function name_lot(table, idx, lot) {
	open_line(table, idx);
	tick_line("use_serial_batch_fields");
	fill_line("batch_no", lot, "Lot");
	close_line();
}

function button(label) {
	settle(); // the form has finished drawing its buttons
	close_onboarding();
	// One query, so a button the form draws again before the click is found again.
	return cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]:visible:first`);
}

function offered(label, expected) {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]:visible`).should(
		expected ? "exist" : "not.exist"
	);
}

function from_menu(group, label) {
	settle();
	const menu = `${FORM} .inner-group-button[data-label="${encodeURIComponent(group)}"]`;
	cy.get(`${menu} > button`).click();
	cy.get(`${menu} .dropdown-item[data-label="${encodeURIComponent(label)}"]`).click();
}

// The form's ⋯ menu, opened to read what it offers, then closed again. Its items are drawn in a
// dropdown of their own, outside the menu's button group.
function form_menu_offers(label, expected) {
	settle();
	cy.get(`${FORM} .menu-btn-group > button:visible:first`).click();
	cy.get(".dropdown-menu.show").should("be.visible");
	cy.get(".dropdown-menu.show .dropdown-item").should(($items) => {
		const labels = $items.toArray().map((item) => item.textContent.trim());
		if (expected) expect(labels, "the menu").to.include(label);
		else expect(labels, "the menu").to.not.include(label);
	});
	cy.get(`${FORM} .menu-btn-group > button:visible:first`).click();
}

function primary_action() {
	settle(); // the toolbar has finished redrawing
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

// Save is pressed and the server refuses it with the sheet's message; the form stays as it was.
function save_refused({ server, screen }, label = "Save") {
	expect_refusal(server);
	primary_action().should("contain", label).click();
	refused_with(screen);
	settle();
	close_dialogs();
}

// The current dialog has finished fading in; a dialog closing behind it may still be fading out
// (found in Chrome, 2026-10-15: the submit confirmation over a closing message).
function opened_fully() {
	cy.window().should((win) => {
		const $wrapper = win.cur_dialog?.$wrapper;
		expect($wrapper?.is(".modal.show"), "a dialog is open").to.equal(true);
		expect($wrapper.css("opacity"), "it has finished opening").to.equal("1");
	});
}

// ERPNext's "Getting Started" panel floats over the right of the form and covers a table's right-hand
// columns (found in CI, 2026-10-15); the person closes it with its cross before going on.
function close_onboarding() {
	cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) =>
		until(() => {
			const $panel = win.$(".onb-panel:visible");
			if (!$panel.length) return true;
			$panel.find(".onb-header-actions button").last()[0]?.click();
			return false;
		}).then((closed) => expect(closed, "the Getting Started panel closes").to.be.true)
	);
}

// Closes whatever dialog is left over the form, the topmost first, as its cross closes it, until
// none is left: the batch picker ERPNext opens late on a line, or a message, would otherwise lie
// over the field the person types into next (found in Chrome, 2026-10-15, in D4-T12). A refusal
// left on screen is a problem of the step and is reported.
function close_dialogs() {
	cy.window({ log: false }).then({ timeout: TIMEOUT + 5000 }, (win) => {
		const reported = new Set();
		return until(() => {
			const open = win.$(".modal.show").length > 0 || win.cur_dialog?.display;
			if (!open) return true;
			const shown = message_dialog(win);
			if (shown?.error && !reported.has(shown.text)) {
				reported.add(shown.text);
				run.problems.push(`the message "${shown.text}" was left on screen`);
			}
			if (shown) win.frappe.hide_msgprint(true);
			else if (win.cur_dialog?.display) win.cur_dialog.hide();
			return false;
		}).then((closed) => expect(closed, "no dialog left over the form").to.be.true);
	});
	close_onboarding();
}

// Waits for the dialog that has just opened to finish opening (standard_flow.js).
function open_dialog() {
	opened_fully();
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the dialog has finished opening").to.equal(true);
		expect(win.cur_dialog.$wrapper.is(".modal.show"), "it is the dialog shown").to.equal(true);
	});
	return cy.get(".modal.show").last();
}

function confirm_submit() {
	primary_action().should("contain", "Submit").click();
	open_dialog().should("contain", "Permanently Submit");
	cy.get(".modal.show .btn-primary").contains("Yes").click();
}

function submit() {
	confirm_submit();
	cy.window().should((win) => expect(current(win).docstatus, "submitted").to.equal(1));
	settle();
}

function submit_refused({ server, screen }) {
	expect_refusal(server);
	confirm_submit();
	refused_with(screen);
	settle();
	close_dialogs(); // the submit confirmation, if it is still open behind the refusal
	cy.window().should((win) => expect(current(win).docstatus, "not submitted").to.equal(0));
}

// A change to a regulated record asks its reason first (audit_reason.js); given when asked.
function give_reason_if_asked(reason) {
	cy.window().then({ timeout: TIMEOUT + 5000 }, (win) =>
		until(
			() =>
				(win.cur_dialog?.display && /^Reason for /.test(win.cur_dialog.title)) ||
				win.frappe.msg_dialog?.$wrapper.is(":visible")
		).then((shown) => {
			expect(shown, "a reason is asked or the answer is shown").to.be.true;
			if (!(win.cur_dialog?.display && /^Reason for /.test(win.cur_dialog.title))) return;
			open_dialog().find('textarea[data-fieldname="reason"]').click().type(reason);
			cy.window()
				.its("cur_dialog")
				.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
			cy.get(".modal.show .btn-primary").contains("Continue").click();
		})
	);
}

// --- The signing dialog (compliance/esign.js) ----------------------------------------------------

function open_signing(label, act = label) {
	settle();
	button(label).click();
	opened_fully();
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
		expect(win.cur_dialog.title).to.equal(`Sign: ${act}`);
	});
}

function enter_reason(reason) {
	cy.get('.modal.show textarea[data-fieldname="reason"]').click().clear().type(reason);
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
}

function enter_password(given = password) {
	cy.get('.modal.show input[data-fieldname="password"]')
		.click()
		.clear()
		.type(given, { log: false });
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("password")).to.equal(given));
}

function press_sign() {
	cy.get(".modal:visible .btn-primary").contains("Sign").click();
}

// A date the act takes, picked in the signing dialog's date field (lot_dates.js).
function pick_date(fieldname, iso) {
	cy.window()
		.its("cur_dialog")
		.then((dialog) => dialog.set_value(fieldname, iso));
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value(fieldname), fieldname).to.equal(iso));
}

// Signs `label` with the reason and the run's password; the form reloads the signed record.
function sign(label, reason) {
	open_signing(label);
	enter_reason(reason);
	enter_password();
	press_sign();
	cy.get(".modal.show").should("not.exist");
	settle(RELOAD_DELAY_MS + QUIET_MS);
}

// The signing dialog is closed by its cross, signing nothing.
function close_signing() {
	cy.window().should((win) => expect(win.frappe.msg_dialog.$wrapper.is(":visible")).to.be.false);
	cy.window().should((win) => expect(win.cur_dialog?.title).to.match(/^Sign: /));
	cy.window()
		.its("cur_dialog")
		.then((dialog) =>
			cy.wrap(dialog.$wrapper.find(".btn-modal-close:visible").first()).click()
		);
	cy.get(".modal.show").should("not.exist");
	settle();
}

// Signs `label` and the server refuses the act with the sheet's message; the dialog is closed.
function sign_refused(label, reason, expected) {
	open_signing(label);
	enter_reason(reason);
	enter_password();
	expect_refusal(expected.server);
	press_sign();
	refused_with(expected.screen);
	close_signing();
}

function banner(color, text) {
	cy.contains(`${FORM} .form-message.${color}`, text).should("be.visible");
}

function signed_as(action, meaning) {
	cy.window().should((win) => {
		const signed = (current(win).__onload?.signatures || []).map((s) => [s.action, s.meaning]);
		expect(signed, "electronic signatures").to.deep.include([action, meaning]);
	});
	cy.contains(FORM, "Electronic signatures").should("be.visible");
}

// --- Reading what is stored, as the signed-in person ----------------------------------------

function stored(doctype, name) {
	return cy.call("frappe.client.get", { doctype, name }).then((r) => r.message);
}

function lot_values(lot, fields) {
	return cy
		.call("frappe.client.get_value", {
			doctype: "Batch",
			filters: { name: lot },
			fieldname: fields,
		})
		.then((r) => r.message);
}

function lots_of(doctype, name) {
	return cy.call(`${JOURNEY}.lots_of`, { doctype, name }).then((r) => r.message);
}

// [lot, from store, to store, quantity] of each lot a stock transaction moved.
function moves_of(doctype, name) {
	return lots_of(doctype, name).then((lots) =>
		lots.map((lot) => [lot.batch_no, lot.from_store, lot.to_store, lot.qty])
	);
}

// [store, quantity, lot status] of each store holding the lot, from Stock by Lot Status.
function stock_of(item, lot) {
	return open_report("Stock by Lot Status", { item_code: item }).then((lines) =>
		lines
			.filter((line) => line.batch_no === lot)
			.map((line) => [line.warehouse, line.qty, line.lot_status])
	);
}

// --- Steps the jobs take again and again ----------------------------------------------------

// QC verifies the supplier certificates on the receipt (lot_control.js).
function qc_verifies(receipt) {
	open_form("Purchase Receipt", receipt);
	button("Verify Certificates").click();
	open_dialog().should("contain", "Verify supplier certificates");
	cy.get(".modal.show .btn-primary").contains("Verify").click();
	cy.get(".modal.show").should("not.exist");
	settle(RELOAD_DELAY_MS + QUIET_MS);
	cy.window().should((win) =>
		current(win).items.forEach((row) =>
			expect(row.mds_certificate_verified, row.item_code).to.equal(1)
		)
	);
}

// QC fills in an incoming inspection of the lot on its receipt, not yet saved.
function inspection_form({ receipt, item, lot }, { dimension = "10.00", sample = 5 } = {}) {
	new_form("Quality Inspection");
	fill("inspection_type", "Incoming", "Select");
	fill("reference_type", "Purchase Receipt", "Select");
	fill("reference_name", receipt, "Link");
	fill("item_code", item, "Link");
	// The item's specification brings its parameters as readings.
	cy.window().should((win) =>
		expect(
			rows(win, "readings").map((row) => row.specification),
			"readings from the specification"
		).to.deep.equal(["Appearance", "Dimension (mm)"])
	);
	fill("batch_no", lot, "Link");
	fill("sample_size", sample);
	fill_row("readings", 1, "reading_value", "Conforms");
	fill_row("readings", 2, "reading_1", dimension);
	fill_row("readings", 2, "mds_instrument", journey.instrument, "Link");
}

// QC inspects the lot on its receipt and submits the inspection; yields its name.
function qc_inspects(lot, { dimension = "10.00", status = "Accepted" } = {}) {
	inspection_form(lot, { dimension });
	save();
	cy.window().should((win) =>
		expect(current(win).status, "the inspection's result").to.equal(status)
	);
	submit();
	return cy.window().then((win) => current(win).name);
}

function new_lot_release(lot) {
	new_form("Lot Release");
	fill("batch", lot, "Link");
	save();
	cy.window().should((win) => expect(current(win).batch).to.equal(lot));
}

// A stock entry filled in on its form, one line, not yet saved.
function stock_entry_form({ type, item, qty, from, to, lot, rate }) {
	new_form("Stock Entry");
	fill("stock_entry_type", type, "Link");
	rows_up_to("items", 1);
	fill_item("items", 1, item);
	if (from) fill_row("items", 1, "s_warehouse", from, "Link");
	if (to) fill_row("items", 1, "t_warehouse", to, "Link");
	fill_row("items", 1, "qty", qty);
	if (rate) fill_row("items", 1, "basic_rate", rate);
	if (lot) name_lot("items", 1, lot);
	settle();
	cy.window().should((win) => {
		const row = rows(win, "items")[0];
		expect(row.s_warehouse || null, "source store").to.equal(from || null);
		expect(row.t_warehouse || null, "target store").to.equal(to || null);
		expect(row.qty, "quantity").to.equal(qty);
	});
}

// A delivery note to the customer filled in on its form, one line of the lot, not yet saved.
function delivery_form({ item, qty, store, lot }) {
	new_form("Delivery Note");
	fill("customer", data.customer, "Link");
	rows_up_to("items", 1);
	fill_item("items", 1, item);
	fill_row("items", 1, "qty", qty);
	fill_row("items", 1, "warehouse", store, "Link");
	name_lot("items", 1, lot);
}

function open_my_work() {
	return open_report("My Work");
}

function lot_not_approved(lot, status, store, hint) {
	return refusal(
		`Lot ${lot} is ${status} and cannot leave ${store}; only an Approved lot can be issued, moved, picked or delivered. ${hint}`,
		"Lot not approved"
	);
}

function kept_apart(lot, store, hint) {
	return refusal(
		`Lot ${lot} is Approved, but its stock in ${store} is kept apart there. ${hint}`,
		"Stock kept apart"
	);
}

const NO_INSPECTION = (lot) =>
	refusal(`Lot ${lot} has no accepted incoming inspection; it cannot be released.`);
const NO_INSPECTION_SINCE_HOLD = (lot) =>
	refusal(
		`Lot ${lot} has no accepted incoming inspection made after it was put on hold; it cannot be released.`
	);
const NOT_FROM_PRODUCTION = (item, voucher) =>
	refusal(
		`${item}: a finished good comes into stock only from production, on its work order's manufacture entry, not on a ${voucher}. Only opening stock at migration comes in otherwise, on a material receipt with Is Opening set to Yes, which QA inspects and releases.`,
		"Finished goods come from production"
	);

// --- The sheet ------------------------------------------------------------------------------

context("D4 Inventory & Batch Ledger: the browser test sheet, as each job", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password })).then(({ message }) => {
			journey = message;
		});
		cy.then(() => cy.call(`${SHEET}.prepare_sheet`, { password })).then(({ message }) => {
			data = message;
		});
	});

	beforeEach(() => {
		run.problems.length = 0;
		run.expected.length = 0;
		watch_requests();
		// A desk call the server refuses rejects its promise with nothing, which some ERPNext form
		// scripts leave unhandled; the refused request itself is reported by watch_requests with the
		// server's message. Any other script error is a problem of the step.
		cy.on("uncaught:exception", (error, _runnable, promise) => {
			const message = error?.message;
			if (!(promise && (message === undefined || message === "undefined"))) {
				run.problems.push(`script error: ${message}`);
			}
			return false;
		});
	});

	it("[URS-QC-001] [URS-STK-004] D4-T01 Stores receives goods into Quarantine with supplier lot number and certificate", () => {
		prepare("prepare_purchase_order", { password }).then((order) => {
			sign_in(STORES);
			open_form("Purchase Order", order.order);
			from_menu("Create", "Purchase Receipt");
			form_is("Purchase Receipt", (doc) => doc.__islocal);
			settle();
			// The line's store changed to the approved store: set back to quarantine, not refused.
			fill_row("items", 1, "warehouse", data.stores.approved, "Link");
			save();
			cy.window().should((win) => {
				expect(rows(win, "items")[0].warehouse, "the line's store").to.equal(
					data.stores.quarantine
				);
				expect(rows(win, "items")[0].qty).to.equal(order.qty);
			});
			// The supplier's lot number and certificate, in the line's own form.
			open_line("items", 1);
			fill_line("mds_supplier_lot_no", "SUP-2026-0417");
			close_line();
			cy.window().then((win) => {
				const row = rows(win, "items")[0];
				// The certificate file, as the attach control uploads it (standard_flow.js).
				upload_private_file("COA-SUP-2026-0417.pdf").then((url) =>
					cy
						.window()
						.then((w) =>
							w.frappe.model.set_value(row.doctype, row.name, "mds_certificate", url)
						)
				);
			});
			cy.window().should(
				(win) => expect(rows(win, "items")[0].mds_certificate, "certificate").to.be.ok
			);
			save();
			submit();
			const made = {};
			cy.window().then((win) => {
				made.receipt = current(win).name;
				made.expiry = shifted(current(win).posting_date, data.shelf_life_days);
			});
			cy.then(() => lots_of("Purchase Receipt", made.receipt)).then((lots) => {
				expect(lots, "lots received").to.have.length(1);
				const [lot] = lots;
				expect(lot.batch_no, "a new lot of the material").to.match(
					new RegExp(`^${data.material}-`)
				);
				expect(lot.to_store).to.equal(data.stores.quarantine);
				expect(lot.expiry_date, "expiry: posting date + shelf life").to.equal(made.expiry);
				made.lot = lot.batch_no;
			});
			cy.then(() => open_form("Batch", made.lot));
			cy.window().should((win) => {
				const lot = current(win);
				expect(lot.mds_lot_status).to.equal("Quarantine");
				for (const field of [
					"mds_status_set_by",
					"mds_status_set_at",
					"mds_status_reason",
					"mds_lot_release",
				]) {
					expect(lot[field], field).to.not.be.ok;
				}
			});
			cy.then(() => open_form("Purchase Receipt", made.receipt));
			cy.window().should((win) => {
				const row = rows(win, "items")[0];
				expect(row.mds_supplier_lot_no).to.equal("SUP-2026-0417");
				expect(row.mds_certificate_verified, "certificate verified").to.equal(0);
			});
			offered("Verify Certificates", false);
			no_problems("D4-T01");
		});
	});

	it("[URS-QC-001] D4-T02 Only QC verifies the certificate; once verified it cannot change", () => {
		prepare("prepare_lot", { password, stage: "received" }).then((lot) => {
			sign_in(STORES);
			open_form("Purchase Receipt", lot.receipt);
			refused_call(`meddevice_suite.lot_control.receipt.verify_certificate`, {
				receipt: lot.receipt,
				row: lot.row,
			}).should("match", exact("Only QC verifies a supplier certificate."));

			sign_in(QC);
			open_form("Purchase Receipt", lot.receipt);
			button("Verify Certificates").click();
			open_dialog().should("contain", "Verify supplier certificates");
			cy.window().then((win) => {
				const row = rows(win, "items")[0];
				const label = `1. ${lot.item} · lot ${
					row.batch_no || "in bundle"
				} · supplier lot ${lot.supplier_lot}`;
				cy.get(".modal.show").should("contain", label);
			});
			cy.window()
				.its("cur_dialog")
				.should((dialog) =>
					expect(dialog.get_value("lines"), "line 1 ticked").to.deep.equal([lot.row])
				);
			cy.get(".modal.show .btn-primary").contains("Verify").click();
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			cy.window().should((win) => {
				const row = rows(win, "items")[0];
				expect(row.mds_certificate_verified).to.equal(1);
				expect(row.mds_certificate_verified_by).to.equal(users[QC]);
				expect(row.mds_certificate_verified_at, "verified at").to.be.ok;
			});
			offered("Verify Certificates", false);

			// The audit trail is QA's to review: the inspector's job cannot open it (see the report).
			sign_in(QA);
			open_form("Purchase Receipt", lot.receipt);
			from_menu("View", "Audit Trail");
			cy.window().should((win) => {
				expect(win.frappe.get_route()[0]).to.equal("List");
				expect(win.cur_list?.doctype).to.equal("Audit Event");
			});
			settle();
			cy.call("frappe.client.get_list", {
				doctype: "Audit Event",
				filters: { reference_doctype: "Purchase Receipt", reference_name: lot.receipt },
				fields: ["action", "user", "reason"],
				limit_page_length: 0,
			}).then(({ message }) =>
				expect(message).to.deep.include({
					action: "Updated after submission",
					user: users[QC],
					reason: "Supplier certificate verified for line 1",
				})
			);

			sign_in(STORES);
			open_form("Purchase Receipt", lot.receipt);
			open_line("items", 1);
			fill_line("mds_supplier_lot_no", "SUP-2026-0418");
			close_line();
			const changed = refusal(
				"The supplier certificate of line 1 has been verified; it cannot be changed."
			);
			expect_refusal(changed.server);
			primary_action().should("contain", "Update").click();
			give_reason_if_asked("Supplier lot number corrected (browser test)");
			refused_with(changed.screen);
			stored("Purchase Receipt", lot.receipt).then((receipt) =>
				expect(receipt.items[0].mds_supplier_lot_no, "unchanged").to.equal(
					lot.supplier_lot
				)
			);
			no_problems("D4-T02");
		});
	});

	it("[URS-QC-001] [URS-QC-002] D4-T03 QC's incoming inspection waits for verification and names its instrument", () => {
		prepare("prepare_lot", { password, stage: "received" }).then((lot) => {
			sign_in(QC);
			inspection_form(lot);
			save_refused(
				refusal(
					`Verify the supplier certificate on ${lot.receipt} line 1 before inspecting lot ${lot.lot}.`,
					"Certificate not verified"
				)
			);
			cy.window().should((win) => expect(win.cur_frm.is_new(), "nothing saved").to.be.ok);

			qc_verifies(lot.receipt);
			inspection_form(lot);
			save();
			cy.window().should((win) => {
				expect(current(win).status).to.equal("Accepted");
				const dimension = rows(win, "readings")[1];
				expect(
					[dimension.min_value, dimension.max_value],
					"the specification's limits"
				).to.deep.equal([9.5, 10.5]);
			});

			clear_row("readings", 2, "mds_instrument");
			save_refused(
				refusal(
					"Reading 2 (Dimension (mm)) is a measurement; name the instrument it was taken with."
				)
			);

			fill_row("readings", 2, "mds_instrument", journey.instrument, "Link");
			fill("sample_size", 2);
			save();
			const small = new RegExp(
				`Specification ${exact(data.specification).source} samples at least ${
					data.sample_size
				}; this inspection samples 2(\\.0+)?\\.`
			);
			submit_refused({ server: small, screen: small });

			fill("sample_size", 5);
			save();
			submit();
			cy.window().should((win) => expect(current(win).status).to.equal("Accepted"));
			no_problems("D4-T03");
		});
	});

	it("[URS-QC-003] [URS-STK-004] D4-T04 Full path: QA releases the inspected lot with a signature", () => {
		prepare("prepare_lot", { password, stage: "verified" }).then((lot) => {
			const made = {};
			const reason = "Incoming inspection accepted";
			sign_in(QA);
			open_form("Batch", lot.lot);
			button("Decide Lot").click();
			form_is("Lot Release", (doc) => doc.__islocal && doc.batch === lot.lot);
			settle();
			save();
			sign_refused("Release", "too early", NO_INSPECTION(lot.lot));
			cy.window().should((win) => {
				expect(current(win).docstatus, "awaiting decision").to.equal(0);
				made.draft = current(win).name;
			});

			sign_in(QC);
			qc_inspects(lot).then((name) => {
				made.inspection = name;
			});

			sign_in(QA);
			cy.then(() => open_form("Lot Release", made.draft));
			open_signing("Release");
			enter_reason(reason);
			enter_password("not-the-password");
			const wrong = refusal("Incorrect password. The signature was not recorded.");
			expect_refusal(wrong.server);
			press_sign();
			refused_with(wrong.screen);
			cy.window().should((win) =>
				expect(current(win).docstatus, "nothing signed").to.equal(0)
			);
			lot_values(lot.lot, ["mds_lot_status"]).then((values) =>
				expect(values.mds_lot_status, "still in quarantine").to.equal("Quarantine")
			);
			// The dialog stays open with the reason; the password is entered again.
			enter_password();
			press_sign();
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus, "signed").to.equal(1);
				expect(doc.decision).to.equal("Release");
				expect(doc.quality_inspection).to.equal(made.inspection);
				expect(doc.decided_by).to.equal(users[QA]);
				made.transfer = doc.stock_entry;
			});
			signed_as("Release", "Released");

			cy.then(() => open_form("Stock Entry", made.transfer));
			cy.window().should((win) => {
				const entry = current(win);
				expect(entry.purpose).to.equal("Material Transfer");
				expect(entry.mds_lot_release_transfer, "posted by a lot release").to.equal(1);
				expect(entry.remarks).to.equal(reason);
				expect(
					entry.items.map((row) => [row.s_warehouse, row.t_warehouse, row.qty])
				).to.deep.equal([[data.stores.quarantine, data.stores.approved, lot.qty]]);
			});
			open_form("Batch", lot.lot);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.mds_lot_status).to.equal("Approved");
				expect(doc.mds_status_set_by).to.equal(users[QA]);
				expect(doc.mds_status_reason).to.equal(reason);
				expect(doc.mds_lot_release).to.equal(made.draft);
			});
			// Last, so the rest of the test is checked first: the decided record's banner is not drawn
			// (lot_release.js reads frm.docstatus, which a form does not have; see the report).
			cy.then(() => open_form("Lot Release", made.draft));
			banner("green", `Release: ${reason}`);
			no_problems("D4-T04");
		});
	});

	it("[URS-QC-003] [URS-STK-010] D4-T05 Quantity rejected at the door stays in the Rejected store", () => {
		const made = {};
		sign_in(STORES);
		new_form("Purchase Receipt");
		fill("supplier", data.supplier, "Link");
		rows_up_to("items", 1);
		fill_item("items", 1, data.material);
		// ERPNext keeps Received Quantity = Accepted + Rejected as either is typed.
		fill_row("items", 1, "qty", 6);
		fill_row("items", 1, "rejected_qty", 2);
		cy.window().should((win) => {
			const row = rows(win, "items")[0];
			expect(
				[row.received_qty, row.qty, row.rejected_qty],
				"received, accepted, rejected"
			).to.deep.equal([8, 6, 2]);
		});
		open_line("items", 1);
		set_line("rejected_warehouse", data.stores.hold);
		fill_line("mds_supplier_lot_no", "SUP-2026-0420");
		close_line();
		cy.window().then((win) => {
			const row = rows(win, "items")[0];
			upload_private_file("COA-SUP-2026-0420.pdf").then((url) =>
				cy
					.window()
					.then((w) =>
						w.frappe.model.set_value(row.doctype, row.name, "mds_certificate", url)
					)
			);
		});
		cy.window().should((win) => expect(rows(win, "items")[0].mds_certificate).to.be.ok);
		save_refused(refusal("Line 1: quantity rejected at receipt goes to the Rejected store."));

		open_line("items", 1);
		set_line("rejected_warehouse", data.stores.rejected);
		close_line();
		save();
		submit();
		cy.window().then((win) => {
			made.receipt = current(win).name;
		});
		cy.then(() => lots_of("Purchase Receipt", made.receipt)).then((lots) => {
			made.lot = lots[0].batch_no;
		});
		cy.then(() => stock_of(data.material, made.lot)).then((held) =>
			expect(held, "one lot in two stores").to.deep.equal([
				[data.stores.quarantine, 6, "Quarantine"],
				[data.stores.rejected, 2, "Quarantine"],
			])
		);
		cy.then(() => {
			stock_entry_form({
				type: "Material Transfer",
				item: data.material,
				qty: 1,
				from: data.stores.rejected,
				to: data.stores.approved,
				lot: made.lot,
			});
			save_refused(
				lot_not_approved(
					made.lot,
					"Quarantine",
					data.stores.rejected,
					"QA releases it after incoming inspection."
				)
			);
		});

		sign_in(QC);
		cy.then(() => qc_verifies(made.receipt));
		cy.then(() => qc_inspects({ receipt: made.receipt, item: data.material, lot: made.lot }));

		sign_in(QA);
		cy.then(() => new_lot_release(made.lot));
		cy.window().should((win) =>
			expect(current(win).quantity, "Quantity in Stock").to.equal(6)
		);
		sign("Release", "Incoming inspection accepted");
		cy.window().should((win) => {
			expect(current(win).docstatus).to.equal(1);
			made.transfer = current(win).stock_entry;
		});
		cy.then(() => moves_of("Stock Entry", made.transfer)).then((moves) =>
			expect(moves, "only the quarantined quantity moves").to.deep.equal([
				[made.lot, data.stores.quarantine, data.stores.approved, 6],
			])
		);

		sign_in(STORES);
		cy.then(() => {
			stock_entry_form({
				type: "Material Transfer",
				item: data.material,
				qty: 1,
				from: data.stores.rejected,
				to: data.stores.approved,
				lot: made.lot,
			});
			save_refused(
				kept_apart(
					made.lot,
					data.stores.rejected,
					"It can only be returned to its supplier, scrapped or moved to the Returns store."
				)
			);
			stock_entry_form({
				type: "Material Issue",
				item: data.material,
				qty: 2,
				from: data.stores.rejected,
				lot: made.lot,
			});
		});
		save();
		submit();
		no_problems("D4-T05");
	});

	it("[URS-QC-003] D4-T06 QA rejects a lot; the stock goes to the Rejected store and a return is drafted for Purchase", () => {
		prepare("prepare_lot", { password, stage: "rejected_inspection" }).then((lot) => {
			const made = {};
			const reason = "Dimension out of limits";
			sign_in(QA);
			new_lot_release(lot.lot);
			sign_refused("Release", "accepted after all", NO_INSPECTION(lot.lot));
			sign("Reject", reason);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus).to.equal(1);
				expect(doc.decision).to.equal("Reject");
				made.decided = doc.quantity;
				made.transfer = doc.stock_entry;
				made.supplier_return = doc.supplier_return;
				made.decision = doc.name;
			});
			signed_as("Reject", "Rejected");
			lot_values(lot.lot, ["mds_lot_status"]).then((values) =>
				expect(values.mds_lot_status).to.equal("Rejected")
			);
			cy.then(() => moves_of("Stock Entry", made.transfer)).then((moves) =>
				expect(moves).to.deep.equal([
					[lot.lot, data.stores.quarantine, data.stores.rejected, lot.qty],
				])
			);

			sign_in(PURCHASE);
			cy.then(() => open_form("Purchase Receipt", made.supplier_return));
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus, "a draft").to.equal(0);
				expect(doc.is_return).to.equal(1);
				expect(doc.return_against).to.equal(lot.receipt);
				expect(
					doc.items.map((row) => [
						row.batch_no,
						row.qty,
						row.warehouse,
						row.rejected_qty,
					])
				).to.deep.equal([[lot.lot, -made.decided, data.stores.rejected, 0]]);
			});
			// Purchase reads the return; submitting it is Stores' (see the report).
			primary_action().should("not.exist");

			sign_in(STORES);
			cy.then(() => open_form("Purchase Receipt", made.supplier_return));
			submit();

			sign_in(QA);
			new_lot_release(lot.lot);
			for (const label of ["Release", "Reject", "Hold"]) offered(label, false);
			open_form("Batch", lot.lot);
			offered("Decide Lot", false);
			// Last, so the rest of the test is checked first: the decided record's banner is not drawn
			// (lot_release.js reads frm.docstatus, which a form does not have; see the report).
			cy.then(() => open_form("Lot Release", made.decision));
			banner("red", `Reject: ${reason}`);
			no_problems("D4-T06");
		});
	});

	it("[URS-QC-003] [URS-STK-005] D4-T07 Hold an approved lot, then release only after a new inspection", () => {
		prepare("prepare_lot", { password, stage: "approved" }).then((lot) => {
			const made = {};
			sign_in(QA);
			new_lot_release(lot.lot);
			offered("Hold", true);
			for (const label of ["Release", "Reject", "Return to Stock", "Reject Return"])
				offered(label, false);
			sign("Hold", "label unreadable");
			cy.window().should((win) => {
				expect(current(win).docstatus).to.equal(1);
				made.transfer = current(win).stock_entry;
				made.hold = current(win).name;
			});
			signed_as("Hold", "Held");
			lot_values(lot.lot, ["mds_lot_status"]).then((values) =>
				expect(values.mds_lot_status).to.equal("Hold")
			);
			cy.then(() => moves_of("Stock Entry", made.transfer)).then((moves) =>
				expect(moves).to.deep.equal([
					[lot.lot, data.stores.approved, data.stores.hold, lot.qty],
				])
			);

			sign_in(STORES);
			stock_entry_form({
				type: "Material Issue",
				item: lot.item,
				qty: 1,
				from: data.stores.hold,
				lot: lot.lot,
			});
			save_refused(
				lot_not_approved(lot.lot, "Hold", data.stores.hold, "QA releases or rejects it.")
			);

			sign_in(QA);
			new_lot_release(lot.lot);
			cy.window().should((win) => {
				expect(current(win).held_since, "On Hold Since").to.be.ok;
				made.draft = current(win).name;
			});
			sign_refused("Release", "looked fine", NO_INSPECTION_SINCE_HOLD(lot.lot));

			sign_in(QC);
			qc_inspects(lot).then((name) => {
				made.inspection = name;
			});

			sign_in(QA);
			cy.then(() => open_form("Lot Release", made.draft));
			sign("Release", "retested and accepted");
			cy.window().should((win) => {
				expect(current(win).docstatus).to.equal(1);
				expect(current(win).quality_inspection).to.equal(made.inspection);
				made.back = current(win).stock_entry;
			});
			lot_values(lot.lot, ["mds_lot_status"]).then((values) =>
				expect(values.mds_lot_status).to.equal("Approved")
			);
			cy.then(() => moves_of("Stock Entry", made.back)).then((moves) =>
				expect(moves).to.deep.equal([
					[lot.lot, data.stores.hold, data.stores.approved, lot.qty],
				])
			);
			// Last, so the rest of the test is checked first: the decided record's banner is not drawn
			// (lot_release.js reads frm.docstatus, which a form does not have; see the report).
			cy.then(() => open_form("Lot Release", made.hold));
			banner("orange", "Hold: label unreadable");
			no_problems("D4-T07");
		});
	});

	it("[URS-QC-003] D4-T08 A stale draft and a newer rejected inspection", () => {
		prepare("prepare_lot", { password, stage: "ready" }).then((first) =>
			prepare("prepare_lot", { password, stage: "ready" }).then((second) => {
				const made = {};
				sign_in(QA);
				new_lot_release(first.lot);
				cy.window().then((win) => {
					made.draft = current(win).name;
				});
				new_lot_release(first.lot);
				sign("Hold", "supplier notice");
				cy.window().should((win) => expect(current(win).docstatus).to.equal(1));
				// The draft made before the hold is read again when QA signs it.
				cy.then(() => open_form("Lot Release", made.draft));
				sign_refused(
					"Release",
					"inspection accepted",
					NO_INSPECTION_SINCE_HOLD(first.lot)
				);

				sign_in(QC);
				qc_inspects(first);
				sign_in(QA);
				cy.then(() => open_form("Lot Release", made.draft));
				sign("Release", "re-inspected after the supplier notice");
				cy.window().should((win) => {
					const doc = current(win);
					expect(doc.docstatus).to.equal(1);
					expect(doc.lot_status_before, "Lot Status at Decision").to.equal("Hold");
					expect(doc.held_since, "On Hold Since").to.be.ok;
					expect(doc.quantity, "the held quantity").to.equal(first.qty);
				});

				// The newest inspection decides: a rejected one after an accepted one.
				sign_in(QC);
				qc_inspects(second, { dimension: "12.00", status: "Rejected" });
				sign_in(QA);
				new_lot_release(second.lot);
				sign_refused("Release", "first inspection accepted", NO_INSPECTION(second.lot));
				no_problems("D4-T08");
			})
		);
	});

	it("[URS-STK-010] D4-T09 A customer's return comes only into the Returns store", () => {
		prepare("prepare_delivery", { qty: 10, delivered: 3 }).then((lot) => {
			sign_in(STORES);
			open_form("Delivery Note", lot.delivery);
			from_menu("Create", "Sales Return");
			form_is("Delivery Note", (doc) => doc.__islocal && doc.is_return);
			settle();
			cy.window().should((win) =>
				expect(
					rows(win, "items")[0].warehouse,
					"the company's store for sales returns"
				).to.equal(data.stores.returns)
			);
			fill_row("items", 1, "warehouse", data.stores.approved, "Link");
			save_refused(
				refusal(
					`Line 1: goods a customer sends back come into the Returns store, where QA decides them; ${data.stores.approved} is not it.`,
					"Stock returned"
				)
			);
			fill_row("items", 1, "warehouse", data.stores.returns, "Link");
			fill_row("items", 1, "qty", -2);
			save();
			submit();
			lot_values(lot.lot, ["mds_lot_status"]).then((values) =>
				expect(values.mds_lot_status).to.equal("Approved")
			);
			stock_of(lot.item, lot.lot).then((held) =>
				expect(held).to.deep.include([data.stores.returns, 2, "Approved"])
			);
			delivery_form({ item: lot.item, qty: 1, store: data.stores.returns, lot: lot.lot });
			save_refused(
				kept_apart(
					lot.lot,
					data.stores.returns,
					"It leaves only by QA's signed disposition: back to stock after an inspection, or to the Rejected store."
				)
			);
			no_problems("D4-T09");
		});
	});

	it("[URS-STK-010] D4-T10 QA decides returned goods: Return to Stock and Reject Return", () => {
		prepare("prepare_delivery", { qty: 10, delivered: 5, returned: 2 }).then((lot) => {
			const made = {};
			sign_in(QA);
			new_lot_release(lot.lot);
			banner(
				"blue",
				"2 of this lot came back from customers and waits in the Returns store."
			);
			offered("Return to Stock", true);
			offered("Reject Return", true);
			cy.window().then((win) => {
				made.draft = current(win).name;
			});
			sign_refused(
				"Return to Stock",
				"returned unopened",
				refusal(
					`Lot ${lot.lot} has no accepted inspection made after its goods came back; they cannot go back to stock.`
				)
			);

			// QC's inspection of the goods that came back (see the report).
			prepare("inspect_returned", { lot: lot.lot, entry: lot.entry });

			sign_in(QA);
			cy.then(() => open_form("Lot Release", made.draft));
			sign("Return to Stock", "returned unopened, inspected");
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.docstatus).to.equal(1);
				expect(doc.decision).to.equal("Return to Stock");
				expect(doc.quantity, "Quantity in Stock").to.equal(2);
				made.back = doc.stock_entry;
			});
			signed_as("Return to Stock", "Released");
			cy.then(() => moves_of("Stock Entry", made.back)).then((moves) =>
				expect(moves).to.deep.equal([
					[lot.lot, data.stores.returns, data.stores.finished_goods, 2],
				])
			);
			lot_values(lot.lot, ["mds_lot_status"]).then((values) =>
				expect(values.mds_lot_status).to.equal("Approved")
			);

			prepare("customer_returns", { delivery: lot.delivery, qty: 1 });
			sign_in(QA);
			new_lot_release(lot.lot);
			sign("Reject Return", "packaging damaged");
			cy.window().should((win) => {
				expect(current(win).docstatus).to.equal(1);
				made.rejected = current(win).stock_entry;
			});
			signed_as("Reject Return", "Rejected");
			cy.then(() => moves_of("Stock Entry", made.rejected)).then((moves) =>
				expect(moves).to.deep.equal([
					[lot.lot, data.stores.returns, data.stores.rejected, 1],
				])
			);
			lot_values(lot.lot, ["mds_lot_status"]).then((values) =>
				expect(values.mds_lot_status).to.equal("Approved")
			);

			new_lot_release(lot.lot);
			sign("Hold", "recall");
			cy.window().should((win) => expect(current(win).docstatus).to.equal(1));
			// The held lot's goods are accepted back into the Returns store.
			prepare("customer_returns", { delivery: lot.delivery, qty: 1 });
			sign_in(QA);
			new_lot_release(lot.lot);
			offered("Return to Stock", false);
			offered("Reject Return", true);
			cy.window().then((win) =>
				refused_call("meddevice_suite.compliance.signing.sign_action", {
					doctype: "Lot Release",
					name: current(win).name,
					action: "Return to Stock",
					password,
					reason: "test",
				}).should(
					"match",
					exact(
						`Lot ${lot.lot} is Hold; goods that came back go back to stock only once the lot is Approved.`
					)
				)
			);
			no_problems("D4-T10");
		});
	});

	it("[URS-STK-005] D4-T11 Quarantine stock cannot be issued, moved, picked or delivered", () => {
		prepare("prepare_quarantined_product", { qty: 10 }).then((lot) => {
			const refused = lot_not_approved(
				lot.lot,
				"Quarantine",
				data.stores.quarantine,
				"QA releases it after incoming inspection."
			);
			sign_in(STORES);
			stock_entry_form({
				type: "Material Transfer",
				item: lot.item,
				qty: 1,
				from: data.stores.quarantine,
				to: data.stores.approved,
				lot: lot.lot,
			});
			save_refused(refused);
			cy.window().should((win) => expect(win.cur_frm.is_new(), "nothing saved").to.be.ok);
			stock_entry_form({
				type: "Material Issue",
				item: lot.item,
				qty: 1,
				from: data.stores.quarantine,
				lot: lot.lot,
			});
			save_refused(refused);
			delivery_form({ item: lot.item, qty: 1, store: data.stores.quarantine, lot: lot.lot });
			save_refused(refused);
			// A pick list line's store is filled in by the pick list itself, not typed (see the report).
			refused_call("frappe.client.insert", {
				doc: {
					doctype: "Pick List",
					company: data.company,
					purpose: "Material Transfer",
					locations: [
						{
							item_code: lot.item,
							qty: 1,
							stock_qty: 1,
							warehouse: data.stores.quarantine,
							batch_no: lot.lot,
							use_serial_batch_fields: 1,
						},
					],
				},
			}).should("match", refused.server);
			stock_of(lot.item, lot.lot).then((held) =>
				expect(held, "unchanged").to.deep.equal([
					[data.stores.quarantine, lot.qty, "Quarantine"],
				])
			);
			no_problems("D4-T11");
		});
	});

	it("[URS-STK-008] D4-T12 An expired lot is not used; it may be set apart in the Hold store", () => {
		prepare("prepare_lot", { password, stage: "approved", item: data.expiry_material }).then(
			(lot) => {
				const made = {};
				const reason = "Expiry date checked against the certificate (test)";
				sign_in(QA);
				open_form("Batch", lot.lot);
				open_signing("Correct Dates");
				cy.window().then((win) => {
					made.made = in_days(win, -30);
					made.expiry = in_days(win, -1);
					made.expiry_shown = user_date(win, made.expiry);
				});
				cy.then(() => pick_date("manufacturing_date", made.made));
				cy.then(() => pick_date("expiry_date", made.expiry));
				enter_reason(reason);
				enter_password();
				press_sign();
				cy.get(".modal.show").should("not.exist");
				settle(RELOAD_DELAY_MS + QUIET_MS);
				cy.window().should((win) => {
					const doc = current(win);
					expect([doc.manufacturing_date, doc.expiry_date]).to.deep.equal([
						made.made,
						made.expiry,
					]);
					expect(doc.mds_dates_corrected_by).to.equal(users[QA]);
					expect(doc.mds_dates_corrected_at, "Dates Corrected At").to.be.ok;
					expect(doc.mds_dates_correction_reason).to.equal(reason);
				});
				cy.contains(FORM, "Date Correction").should("be.visible");

				sign_in(STORES);
				cy.then(() => {
					const expired = refusal(
						`Lot ${lot.lot} expired on ${made.expiry_shown}; it cannot be issued, used or delivered. Move it to the hold store; QA decides it.`,
						"Lot expired"
					);
					stock_entry_form({
						type: "Material Issue",
						item: lot.item,
						qty: 1,
						from: data.stores.approved,
						lot: lot.lot,
					});
					save();
					submit_refused(expired);
					stock_entry_form({
						type: "Material Transfer",
						item: lot.item,
						qty: 1,
						from: data.stores.approved,
						to: data.stores.finished_goods,
						lot: lot.lot,
					});
					save();
					submit_refused(expired);
					stock_entry_form({
						type: "Material Transfer",
						item: lot.item,
						qty: 1,
						from: data.stores.approved,
						to: data.stores.hold,
						lot: lot.lot,
					});
				});
				save();
				submit();
				cy.window().then((win) =>
					moves_of("Stock Entry", current(win).name).then((moves) =>
						expect(moves).to.deep.equal([
							[lot.lot, data.stores.approved, data.stores.hold, 1],
						])
					)
				);
				no_problems("D4-T12");
			}
		);
	});

	it("[URS-STK-009] [URS-STK-002] D4-T13 First expiry, first out for a lot named by hand, with a stated exception", () => {
		prepare("prepare_fefo", { password }).then((lots) => {
			const made = {};
			sign_in(STORES);
			stock_entry_form({
				type: "Material Issue",
				item: lots.item,
				qty: 1,
				from: data.stores.approved,
			});
			save();
			submit();
			cy.window().then((win) =>
				lots_of("Stock Entry", current(win).name).then((taken) =>
					expect(
						taken.map((lot) => lot.batch_no),
						"the lot that expires first"
					).to.deep.equal([lots.newer])
				)
			);

			stock_entry_form({
				type: "Material Issue",
				item: lots.item,
				qty: 1,
				from: data.stores.approved,
				lot: lots.older,
			});
			save();
			cy.window().then((win) => {
				made.entry = current(win).name;
				made.newer_expiry = user_date(win, lots.newer_expiry);
			});
			cy.then(() =>
				submit_refused(
					refusal(
						`Lot ${lots.newer} expires on ${made.newer_expiry}, before lot ${lots.older}, and would still be in ${data.stores.approved}; the lot that expires first goes first. Take lot ${lots.newer}, or state the FEFO Exception Reason.`,
						"First expiry, first out"
					)
				)
			);
			fill("mds_fefo_reason", "customer asked for this lot");
			save();
			submit();

			sign_in(QA);
			open_report("Lots Taken Out of Expiry Order").then((lines) => {
				const line = lines.find((row) => row.document === made.entry);
				expect(line, "the entry is listed").to.be.ok;
				expect(line.posting_date, "its date").to.be.ok;
				expect(line.reason).to.equal("customer asked for this lot");
				expect(line.owner, "Made By").to.equal(users[STORES]);
			});
			no_problems("D4-T13");
		});
	});

	it("[URS-MST-013] D4-T14 QA approves a store; an unapproved store moves no stock; changes need change control", () => {
		prepare("unique_tag").then((tag) => {
			const made = {};
			sign_in(QA);
			new_form("Warehouse");
			fill("warehouse_name", `Test Store ${tag}`);
			fill("company", data.company, "Link");
			fill("parent_warehouse", data.stores.root, "Link");
			save();
			cy.window().then((win) => {
				made.store = current(win).name;
			});
			banner(
				"orange",
				"Not yet approved by QA, so it is not used yet. Ready for QA's approval."
			);

			sign_in(STORES);
			cy.then(() => {
				stock_entry_form({
					type: "Material Receipt",
					item: data.material,
					qty: 1,
					to: made.store,
					rate: 1450,
				});
				save();
				submit_refused(
					refusal(
						`Store ${made.store} is not approved by QA; no stock moves into or out of it. QA approves it on the store's form (Approve).`,
						"Store not approved"
					)
				);
			});

			sign_in(QA);
			cy.then(() => open_form("Warehouse", made.store));
			sign("Approve", "area checked");
			banner("green", "Approved by QA; what was approved changes under change control.");
			cy.window().should((win) => {
				expect(current(win).mds_master_approval, "Approval by QA").to.equal("Approved");
				expect(current(win).mds_master_approved_by, "Approved By").to.equal(users[QA]);
			});
			signed_as("Approve", "Approved");

			// The store is disabled with the form's own Disable button, which ticks Disabled and saves;
			// the Disabled field itself is not shown on the form.
			cy.then(() => {
				const refused = refusal(
					`Warehouse ${made.store} is in use: name the approved change request that lists it to change it.`,
					"Change control"
				);
				expect_refusal(refused.server);
				button("Disable").click();
				give_reason_if_asked("Store closed (browser test)");
				refused_with(refused.screen);
			});
			cy.then(() => stored("Warehouse", made.store)).then((store) =>
				expect(store.disabled, "still in use").to.equal(0)
			);
			no_problems("D4-T14");
		});
	});

	it("[URS-STK-005] D4-T15 One store per role per company; a group cannot be a role store", () => {
		prepare("unique_tag").then((tag) => {
			sign_in(QA);
			open_list("Warehouse", { mds_store_role: "Quarantine", company: data.company });
			cy.window().should((win) =>
				expect(
					win.cur_list.data.map((row) => row.name),
					"the Quarantine stores"
				).to.deep.equal([data.stores.quarantine])
			);
			cy.call("frappe.client.get_value", {
				doctype: "Warehouse",
				filters: { name: data.stores.quarantine },
				fieldname: "is_group",
			}).then(({ message }) => expect(message.is_group, "not a group").to.equal(0));

			new_form("Warehouse");
			fill("warehouse_name", `Second Quarantine ${tag}`);
			fill("company", data.company, "Link");
			fill("mds_store_role", "Quarantine", "Select");
			save_refused(
				refusal(
					`${data.stores.quarantine} is already the Quarantine store of ${data.company}.`
				)
			);

			// Is Group Warehouse cannot be ticked on ERPNext's form; a store becomes a group with the
			// form's Convert to Group, once saved.
			new_form("Warehouse");
			fill("warehouse_name", `Hold Group ${tag}`);
			fill("company", data.company, "Link");
			fill("parent_warehouse", data.stores.root, "Link");
			save();
			button("Convert to Group").click();
			cy.window().should((win) =>
				expect(current(win).is_group, "a group of stores").to.equal(1)
			);
			settle(RELOAD_DELAY_MS + QUIET_MS);
			fill("mds_store_role", "Hold", "Select");
			save_refused(
				refusal("A Hold store holds stock itself; a group of stores cannot be one.")
			);
			no_problems("D4-T15");
		});
	});

	it("[URS-STK-004] D4-T16 Nobody edits a lot's status, not even QA or the Administrator", () => {
		prepare("prepare_lot", { password, stage: "received" }).then((lot) => {
			const change = {
				doctype: "Batch",
				name: lot.lot,
				fieldname: "mds_lot_status",
				value: "Approved",
			};
			sign_in(QA);
			open_form("Batch", lot.lot);
			cy.window().should((win) =>
				expect(
					win.cur_frm.get_docfield("mds_lot_status").read_only,
					"Lot Status read-only"
				).to.equal(1)
			);
			refused_call("frappe.client.set_value", change).should(
				"match",
				exact(
					`The status of lot ${lot.lot} changes only through a QA release or disposition.`
				)
			);
			fill("description", "silicone, drum 3");
			save();
			cy.window().should((win) =>
				expect(current(win).description).to.equal("silicone, drum 3")
			);

			sign_in(SYSTEM_ADMINISTRATOR);
			open_form("Batch", lot.lot);
			refused_call("frappe.client.set_value", change);
			lot_values(lot.lot, ["mds_lot_status"]).then((values) =>
				expect(values.mds_lot_status, "still Quarantine").to.equal("Quarantine")
			);
			no_problems("D4-T16");
		});
	});

	it("[URS-STK-004] [URS-STK-005] D4-T17 Work orders draw from approved stores and finish into Quarantine", () => {
		sign_in(PRODUCTION);
		new_form("Work Order");
		fill("production_item", data.product, "Link");
		cy.window().should((win) => expect(current(win).bom_no, "the product's bill").to.be.ok);
		fill("qty", 100);
		// Set after the item and its bill have loaded: each reloads the form and clears them.
		fill("wip_warehouse", data.stores.work_in_progress, "Link");
		fill("fg_warehouse", data.stores.quarantine, "Link");
		fill("source_warehouse", data.stores.quarantine, "Link");
		save_refused(
			refusal(
				`${data.stores.quarantine} is a Quarantine store; a work order draws materials from an approved store.`
			)
		);
		fill("source_warehouse", data.stores.approved, "Link");
		fill("wip_warehouse", data.stores.work_in_progress, "Link");
		fill("fg_warehouse", data.stores.finished_goods, "Link");
		save_refused(
			refusal(
				`The finished lot goes to the Quarantine store until QA releases it; ${data.stores.finished_goods} is not one.`
			)
		);
		fill("fg_warehouse", data.stores.quarantine, "Link");
		save();
		no_problems("D4-T17");
	});

	it("[URS-STK-005] [URS-QC-003] D4-T18 QA posts no stock by hand; a release's transfer is never cancelled", () => {
		prepare("prepare_lot", { password, stage: "approved" }).then((lot) => {
			// QA's job may not even pick a stock entry type on the form (its lookup is refused), so the
			// entry QA fills in by hand is sent as QA's request, as the form would save it (see the report).
			sign_in(QA);
			new_form("Stock Entry");
			refused_call("frappe.client.insert", {
				doc: {
					doctype: "Stock Entry",
					stock_entry_type: "Material Transfer",
					company: data.company,
					items: [
						{
							item_code: lot.item,
							qty: 1,
							s_warehouse: data.stores.approved,
							t_warehouse: data.stores.work_in_progress,
							batch_no: lot.lot,
							use_serial_batch_fields: 1,
						},
					],
				},
			}).should(
				"match",
				exact(
					"Stock is posted by Stores and Production; QA moves a lot through its release."
				)
			);

			// Frappe's Cancel would first offer to cancel the signed release that links the entry
			// (Cancel All), so the entry's own cancellation is sent as Management's request (see the report).
			sign_in(MANAGEMENT);
			open_form("Stock Entry", lot.transfer);
			cy.window().should((win) => expect(current(win).mds_lot_release_transfer).to.equal(1));
			refused_call("frappe.client.cancel", {
				doctype: "Stock Entry",
				name: lot.transfer,
			}).should(
				"match",
				exact(
					`${lot.transfer} was posted by a lot release and is not cancelled; put the lot on hold instead.`
				)
			);
			stored("Stock Entry", lot.transfer).then((entry) =>
				expect(entry.docstatus).to.equal(1)
			);

			sign_in(QA);
			open_form("Lot Release", lot.release);
			offered("Cancel", false);
			form_menu_offers("Delete", false);
			refused_call("frappe.client.cancel", { doctype: "Lot Release", name: lot.release });
			refused_call("frappe.client.delete", { doctype: "Lot Release", name: lot.release });
			stored("Lot Release", lot.release).then((decision) =>
				expect(decision.docstatus).to.equal(1)
			);
			no_problems("D4-T18");
		});
	});

	it("[URS-STK-004] [URS-STK-010] D4-T19 New stock never enters an existing lot or an approved store unreleased", () => {
		prepare("prepare_lot", { password, stage: "approved" }).then((lot) => {
			sign_in(STORES);
			stock_entry_form({
				type: "Material Receipt",
				item: lot.item,
				qty: 5,
				to: data.stores.approved,
				lot: lot.lot,
				rate: 1450,
			});
			save();
			submit_refused(
				refusal(
					`Lot ${lot.lot} came in on Purchase Receipt ${lot.receipt}; new stock comes in as a new lot.`,
					"New lot needed"
				)
			);

			stock_entry_form({
				type: "Material Receipt",
				item: lot.item,
				qty: 1,
				to: data.stores.approved,
				rate: 1450,
			});
			save();
			const unreleased = new RegExp(
				`Lot \\S+ is Quarantine and cannot come into ${
					exact(data.stores.approved).source
				}, an approved store; new stock comes into the Quarantine store until QA releases it\\.`
			);
			submit_refused({ server: unreleased, screen: unreleased });

			new_form("Purchase Receipt");
			fill("supplier", data.supplier, "Link");
			rows_up_to("items", 1);
			fill_item("items", 1, lot.item);
			fill_row("items", 1, "qty", 2);
			name_lot("items", 1, lot.lot);
			save_refused(
				refusal(
					`Line 1: lot ${lot.lot} came in on Purchase Receipt ${lot.receipt}; a delivery comes in as a new lot.`,
					"New lot needed"
				)
			);
			no_problems("D4-T19");
		});
	});

	it("[URS-STK-010] D4-T20 Approved stock set apart in Hold cannot be used, and approved stock cannot be put into Quarantine", () => {
		prepare("prepare_lot", { password, stage: "approved", qty: 10 }).then((lot) => {
			sign_in(STORES);
			stock_entry_form({
				type: "Material Transfer",
				item: lot.item,
				qty: 2,
				from: data.stores.approved,
				to: data.stores.hold,
				lot: lot.lot,
			});
			save();
			submit();

			stock_entry_form({
				type: "Material Issue",
				item: lot.item,
				qty: 1,
				from: data.stores.hold,
				lot: lot.lot,
			});
			save_refused(
				kept_apart(
					lot.lot,
					data.stores.hold,
					"It leaves only by QA's signed release, after a new inspection, or rejection."
				)
			);

			stock_entry_form({
				type: "Material Transfer",
				item: lot.item,
				qty: 1,
				from: data.stores.approved,
				to: data.stores.quarantine,
				lot: lot.lot,
			});
			save_refused(
				refusal(
					`Lot ${lot.lot} is Approved and cannot be moved from ${data.stores.approved} into the Quarantine store; only QA's signed decision or a customer's return puts stock there.`,
					"Stock kept apart"
				)
			);
			no_problems("D4-T20");
		});
	});

	it("[URS-STK-011] [URS-STK-012] D4-T21 A lot's dates are fixed once it has stock; QA corrects them with a signature", () => {
		prepare("prepare_lot", { password, stage: "approved" }).then((lot) => {
			const made = {};
			const reason = "Expiry from the supplier certificate (test)";
			sign_in(QA);
			open_form("Batch", lot.lot);
			cy.window().should((win) => {
				for (const field of ["manufacturing_date", "expiry_date"]) {
					expect(win.cur_frm.get_docfield(field).read_only, field).to.equal(1);
				}
				made.made = current(win).manufacturing_date;
				made.expiry = current(win).expiry_date;
			});
			offered("Decide Lot", true);
			offered("Correct Dates", true);
			cy.then(() =>
				refused_call("frappe.client.set_value", {
					doctype: "Batch",
					name: lot.lot,
					fieldname: "expiry_date",
					value: shifted(made.expiry, 365),
				}).should(
					"match",
					exact(
						`Lot ${lot.lot} has stock, so its manufacturing and expiry dates can no longer be edited; QA corrects a wrong date with the signed act Correct Dates, giving the reason.`
					)
				)
			);

			open_form("Batch", lot.lot);
			open_signing("Correct Dates");
			cy.get(".modal.show").should("contain", "Approved");
			cy.window()
				.its("cur_dialog")
				.should((dialog) => {
					expect(dialog.get_value("manufacturing_date"), "as shown").to.equal(made.made);
					expect(dialog.get_value("expiry_date"), "as shown").to.equal(made.expiry);
				});
			cy.then(() => pick_date("expiry_date", shifted(made.expiry, 30)));
			enter_reason(reason);
			enter_password("not-the-password");
			const wrong = refusal("Incorrect password. The signature was not recorded.");
			expect_refusal(wrong.server);
			press_sign();
			refused_with(wrong.screen);
			cy.then(() =>
				lot_values(lot.lot, ["expiry_date"]).then((values) =>
					expect(values.expiry_date, "unchanged").to.equal(made.expiry)
				)
			);
			enter_password();
			press_sign();
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.expiry_date).to.equal(shifted(made.expiry, 30));
				expect(doc.mds_dates_corrected_by).to.equal(users[QA]);
				expect(doc.mds_dates_corrected_at).to.be.ok;
				expect(doc.mds_dates_correction_reason).to.equal(reason);
				expect(doc.mds_lot_status, "Lot Status unchanged").to.equal("Approved");
			});
			signed_as("Correct Dates", "Approved");

			from_menu("View", "Audit Trail");
			cy.window().should((win) => expect(win.cur_list?.doctype).to.equal("Audit Event"));
			settle();
			cy.call("frappe.client.get_list", {
				doctype: "Audit Event",
				filters: { reference_doctype: "Batch", reference_name: lot.lot, reason },
				fields: ["action", "user", "changes"],
				limit_page_length: 0,
			}).then(({ message }) => {
				const event = message.find((row) => row.action === "Updated");
				expect(event, "an Updated event with the reason").to.be.ok;
				expect(event.user).to.equal(users[QA]);
				const changes =
					typeof event.changes === "string"
						? event.changes
						: JSON.stringify(event.changes);
				expect(changes, "old and new expiry")
					.to.contain(made.expiry)
					.and.contain(shifted(made.expiry, 30));
			});
			no_problems("D4-T21");
		});
	});

	it("[URS-STK-012] D4-T22 Correct Dates refuses nonsense dates, a missing reason and anyone but QA", () => {
		prepare("prepare_lot", { password, stage: "approved" }).then((lot) => {
			const made = {};
			sign_in(QA);
			open_form("Batch", lot.lot);
			cy.window().then((win) => {
				made.made = current(win).manufacturing_date;
				made.expiry = current(win).expiry_date;
				made.tomorrow = in_days(win, 1);
			});
			open_signing("Correct Dates");
			const attempt = (expected) => {
				enter_password();
				expect_refusal(expected.server);
				press_sign();
				refused_with(expected.screen);
			};
			cy.then(() => pick_date("manufacturing_date", made.tomorrow));
			enter_reason("Dates checked (browser test)");
			attempt(refusal(`Lot ${lot.lot} cannot have been made after today.`));
			cy.then(() => pick_date("manufacturing_date", made.made));
			cy.then(() => pick_date("expiry_date", shifted(made.made, -1)));
			attempt(refusal(`Lot ${lot.lot} cannot expire before it was made.`));
			cy.then(() => pick_date("expiry_date", made.expiry));
			attempt(
				refusal(`Lot ${lot.lot} already has these dates; there is nothing to correct.`)
			);

			// Without a reason the dialog does not go on; the server refuses it too.
			cy.then(() => pick_date("expiry_date", shifted(made.expiry, 10)));
			cy.get('.modal.show textarea[data-fieldname="reason"]').clear();
			enter_password();
			press_sign();
			refused_with(/Missing Values Required.*Reason/);
			close_signing();
			cy.then(() =>
				refused_call("meddevice_suite.compliance.signing.sign_action", {
					doctype: "Batch",
					name: lot.lot,
					action: "Correct Dates",
					password,
					reason: "",
					meaning: "Approved",
					values: { expiry_date: shifted(made.expiry, 10) },
				}).should(
					"match",
					exact("Give the reason; it is kept on the lot and in the audit trail.")
				)
			);

			refused_call("frappe.client.set_value", {
				doctype: "Batch",
				name: lot.lot,
				fieldname: "mds_dates_corrected_by",
				value: users[STORES],
			}).should(
				"match",
				exact(
					"A correction of a lot's dates is recorded only by QA's signed Correct Dates."
				)
			);

			sign_in(STORES);
			open_form("Batch", lot.lot);
			offered("Correct Dates", false);
			cy.then(() =>
				refused_call("meddevice_suite.compliance.signing.sign_action", {
					doctype: "Batch",
					name: lot.lot,
					action: "Correct Dates",
					password,
					reason: "x",
					meaning: "Approved",
					values: { expiry_date: shifted(made.expiry, 20) },
				}).should("match", exact("Only QA corrects the dates of a lot."))
			);
			lot_values(lot.lot, ["expiry_date"]).then((values) =>
				expect(values.expiry_date).to.equal(made.expiry)
			);
			// No signature on the lot was recorded, checked by the Administrator who reviews them.
			no_problems("D4-T22");
			as_administrator();
			cy.call("frappe.client.get_count", {
				doctype: "Electronic Signature",
				filters: { reference_doctype: "Batch", reference_name: lot.lot },
			}).then(({ message }) => expect(message, "signatures on the lot").to.equal(0));
		});
	});

	it("[URS-STK-013] D4-T23 A finished good is not received on a Purchase Receipt", () => {
		sign_in(STORES);
		new_form("Purchase Receipt");
		fill("supplier", data.supplier, "Link");
		rows_up_to("items", 1);
		// The line's item lookup lists purchase items only: the finished good is not among them.
		settle();
		cy.get(`${cell("items", 1, "item_code")}:first`).click();
		cy.get(first_of(inputs_of(cell("items", 1, "item_code")))).should("have.focus");
		cy.get(first_of(inputs_of(cell("items", 1, "item_code")))).type(data.product, {
			delay: 0,
		});
		settle();
		cy.get("body").should(($body) => {
			const offered_items = $body
				.find('[role="listbox"]:visible [role="option"] strong')
				.toArray()
				.map((option) => option.textContent.trim());
			expect(offered_items, "items offered").to.not.include(data.product);
		});
		close_open_lists();
		// Received all the same (a code scanned in, or the API), it is refused (see the report).
		refused_call("frappe.client.insert", {
			doc: {
				doctype: "Purchase Receipt",
				supplier: data.supplier,
				company: data.company,
				items: [{ item_code: data.product, qty: 10, warehouse: data.stores.quarantine }],
			},
		}).should("match", exact(`The item ${data.product} is not marked as purchase item.`));
		no_problems("D4-T23");
	});

	it("[URS-STK-013] D4-T24 Opening stock of a finished good comes in only as a Material Receipt with Is Opening = Yes", () => {
		sign_in(STORES);
		stock_entry_form({
			type: "Material Receipt",
			item: data.product,
			qty: 5,
			to: data.stores.quarantine,
			rate: 98,
		});
		cy.window().should((win) => expect(current(win).is_opening || "No").to.equal("No"));
		save_refused(NOT_FROM_PRODUCTION(data.product, "Stock Entry"));
		fill("is_opening", "Yes", "Select");
		open_line("items", 1);
		set_line("expense_account", data.opening_account);
		close_line();
		save();
		submit();
		cy.window().then((win) =>
			lots_of("Stock Entry", current(win).name).then((lots) => {
				expect(lots, "a new lot").to.have.length(1);
				expect(lots[0].to_store).to.equal(data.stores.quarantine);
				lot_values(lots[0].batch_no, ["item", "mds_lot_status"]).then((values) =>
					expect(values).to.deep.equal({
						item: data.product,
						mds_lot_status: "Quarantine",
					})
				);
			})
		);
		no_problems("D4-T24");
	});

	it("[URS-STK-013] D4-T25 A stock count does not bring a new finished-goods lot in", () => {
		prepare("prepare_new_product_lot").then((lot) => {
			const refused = NOT_FROM_PRODUCTION(data.product, "Stock Reconciliation");
			sign_in(MANAGEMENT);
			new_form("Stock Reconciliation");
			fill("purpose", "Stock Reconciliation", "Select");
			rows_up_to("items", 1);
			fill_item("items", 1, data.product);
			fill_row("items", 1, "warehouse", data.stores.quarantine, "Link");
			// The lot first: naming it reads its stock into the line (none, a new lot).
			name_lot("items", 1, lot.lot);
			// In the line's own form: the table's right-hand columns lie under the desk's side panel.
			open_line("items", 1);
			fill_line("qty", 10);
			fill_line("valuation_rate", 50);
			close_line();
			save_refused(refused);
			fill("purpose", "Opening Stock", "Select");
			fill("expense_account", data.opening_account, "Link");
			save_refused(refused);
			no_problems("D4-T25");
		});
	});

	it("[URS-PLT-017] [URS-STK-010] D4-T26 QA's My Work lists goods a customer sent back until QA decides them", () => {
		prepare("prepare_delivery", { qty: 10, delivered: 3, returned: 2 }).then((lot) => {
			const waiting = new RegExp(
				`^Lot ${exact(lot.lot).source}: decide on 2(\\.0+)? returned to ${
					exact(data.stores.returns).source
				}$`
			);
			const line_of = (lines) =>
				lines.find((row) => row.doctype === "Batch" && waiting.test(row.what));
			sign_in(QA);
			open_my_work().then((lines) => {
				const line = line_of(lines);
				expect(line, "the returned goods are listed").to.be.ok;
				expect(line.name, "the line opens the lot").to.equal(lot.lot);
			});
			// The line's record link opens the lot.
			cy.window().then((win) => win.frappe.set_route("Form", "Batch", lot.lot));
			form_is("Batch", (doc) => doc.name === lot.lot);
			settle();
			button("Decide Lot").click();
			form_is("Lot Release", (doc) => doc.__islocal && doc.batch === lot.lot);
			settle();
			save();
			sign("Reject Return", "packaging damaged");
			cy.window().should((win) => expect(current(win).docstatus).to.equal(1));
			open_my_work().then(
				(lines) => expect(line_of(lines), "the line is gone").to.not.be.ok
			);

			sign_in(STORES);
			open_my_work().then(
				(lines) =>
					expect(
						lines.filter(
							(row) => row.name === lot.lot && /returned to/.test(row.what)
						),
						"Stores is not asked to decide"
					).to.be.empty
			);
			no_problems("D4-T26");
		});
	});
});

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

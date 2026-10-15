// Browser test sheet of data store D9, Documents: controlled documents and their revisions (module
// 30), the important files repository (module 61), change control (module 36) and the device master
// record (module 31) (meddevice_suite/qms/document_control.py, change_control.py,
// device_master_record.py, meddevice_suite/files_repository/files.py, compliance/signing.py). One
// test per line of the sheet whose id starts D9-DOC, D9-FILE, D9-CC or D9-DMR, in its order, each
// signed in as the job the sheet names and done on the screens as that person would, signing in the
// signing dialog with their own password. Refusals are checked word for word; a refusal only an API
// call can reach (the form does not offer the act) is posted as that job.
//
// The sheet names the pilot's users and records; the test site has none, so the run uses the job
// users of meddevice_suite/qa/ui_users.py (qa.manager@demo.example.com -> the QA Manager job user,
// and so on) and records of its own (meddevice_suite/qa/sheets/d9a.py): "a pilot finished good" ->
// the sheet's own product UIJ-D9-FC-2W-SIL-12, whose Current device master record pins a Packaging
// and Labelling document of its own, so that putting it out of date leaves the standard flow's
// product as it is.
// Each test starts from records d9a.py makes for it through the code's own services, each step as
// the job that does it, so the tests stand on their own where the sheet builds one on another
// (D9-DOC-02 on D9-DOC-01, D9-CC-03 on D9-CC-02, ...). A wait the sheet names (a revision's effective
// date ten days on, a review due two years on) is stood in for on the test site by the set-up.
// "Print and choose the format" is the print view of the record in that format, as the desk's
// Print opens it (document_control.js).

const USERS = "meddevice_suite.qa.ui_users";
const SHEET = "meddevice_suite.qa.sheets.d9a";
const SIGNING = "meddevice_suite.compliance.signing.sign_action";
const CHANGE_CONTROL = "meddevice_suite.qms.change_control";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const TIMEOUT = 20000; // the longest a page or an action may take
const QUIET_MS = 300; // no request in flight for this long: the page has finished
const RELOAD_DELAY_MS = 1000; // the desk reloads a form this long after the server changes it

const QA = "QA Manager";
const QC = "QC Inspector";
const PRODUCTION = "Production Supervisor";
const PURCHASE = "Purchase";
const STORES = "Stores";
const SALES = "Sales";
const MANAGEMENT = "Management";

const DOCUMENT = "Controlled Document";
const REVISION = "Document Revision";
const CHANGE = "Change Request";
const DMR = "Device Master Record";
const FILE = "Important File";
const IMPACTS = [
	"impact_product_quality",
	"impact_risk",
	"impact_regulatory",
	"impact_validation",
	"impact_training",
	"impact_documents",
	"impact_stock",
];
const MEASURING = "Measuring and Monitoring"; // the heading D9-DMR-02 leaves empty

let password;
let users;
let data; // what the sheet works with (qa/sheets/d9a.py prepare_sheet)

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

// The Administrator who runs the tests prepares what a test starts from (qa/sheets/d9a.py).
function prepare(method, args = {}) {
	as_administrator();
	return cy.call(`${SHEET}.${method}`, args).then((r) => r.message);
}

// A session kept between tests is checked before it is used: one the server has since ended
// (found in Chrome, 2026-10-15: the Administrator's, late in the run) is signed in again.
function signed_in_as(user, pwd) {
	cy.session(
		["sheet-d9a", user],
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
	// A refusal of the record type's permission comes as its own message (frappe.flags.error_message).
	if (response._error_message) messages.push(plain(response._error_message));
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

// Empties the field and types `value`, finding the field again for each step: clearing it can make
// the form redraw it (found in Chrome, 2026-10-15, on the work order's stores).
function type_afresh(selector, value) {
	settle();
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
	// Typed once the clicked cell has the cursor (standard_flow.js); a choice is chosen from its
	// list, which needs no cursor (a second line's Select did not take it: D9-CC-08, 2026-10-16).
	if (kind !== "Select") {
		cy.get(inputs_of(cell(table, idx, fieldname)))
			.first()
			.should("have.focus");
	}
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

// --- The signing dialog (compliance/esign.js) ----------------------------------------------------

// Opens the signing dialog of the act `act` with the form's button `label` (or another way of
// opening it, a menu item), once it has finished opening.
function open_signing(label, act = label, open = () => button(label).click()) {
	settle();
	open();
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

// Signs `label` (the act `act`) with the run's password, and the reason when the act asks one; the
// form reloads the signed record.
function sign(label, reason = null, act = label, open = undefined) {
	open_signing(label, act, open);
	if (reason) enter_reason(reason);
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
function sign_refused(label, reason, expected, act = label) {
	open_signing(label, act);
	if (reason) enter_reason(reason);
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

// --- What this sheet does again and again ------------------------------------------------------

// A name of this run's own for what a test makes on the screens.
function unique() {
	return `${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
}

function today(win) {
	return win.frappe.datetime.get_today();
}

function two_digits(number) {
	return String(number).padStart(2, "0");
}

// A file uploaded by the signed-in user, as the attach control uploads it; its address. Each upload
// has content of its own (the fixture with a comment line of its own after its end), so that a
// corrected file is a new file with a new fingerprint, never the earlier one again.
function upload_file(name, { is_private = true } = {}) {
	return cy.fixture("controlled-document.pdf", null).then((pdf) =>
		cy.window({ log: false }).then((win) => {
			const content = Cypress.Buffer.concat([
				Cypress.Buffer.from(pdf),
				Cypress.Buffer.from(`\n% ${name} ${unique()}\n`),
			]);
			const form = new win.FormData();
			form.append("file", new win.Blob([content], { type: "application/pdf" }), name);
			form.append("is_private", is_private ? "1" : "0");
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

// The file attached in an Attach field of the form, as the attach control sets it once uploaded.
function attach(fieldname, name, options = {}) {
	settle();
	upload_file(name, options).then((url) => {
		cy.window().then((win) => win.cur_frm.set_value(fieldname, url));
		cy.window().should((win) => expect(current(win)[fieldname], fieldname).to.equal(url));
	});
	settle();
}

// A field emptied, as a person deletes what it holds.
function clear_field(fieldname) {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(fieldname);
	const selector = inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`);
	cy.get(first_of(selector)).clear();
	cy.get(first_of(selector)).blur();
	cy.window().should((win) => expect(current(win)[fieldname], fieldname).to.not.be.ok);
}

// A job chosen in a "Jobs That ..." field (a table of roles, filled by choosing from its lookup).
function fill_jobs(fieldname, role) {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(fieldname);
	choose(`${FORM} .frappe-control[data-fieldname="${fieldname}"] input:visible`, role);
	cy.window().should((win) =>
		expect(
			(current(win)[fieldname] || []).map((row) => row.role),
			fieldname
		).to.include(role)
	);
	close_open_lists();
}

// What the lookup of a field lists for `text`, read once the search has answered and the list has
// stopped changing; the field is emptied and left again, as the person leaves it.
function lookup_offers(fieldname, text) {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(fieldname);
	const selector = inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`);
	type_afresh(selector, text);
	settle();
	return cy
		.window({ log: false })
		.then({ timeout: TIMEOUT + 5000 }, (win) => {
			let last = null;
			let since = Date.now();
			return until(() => {
				const listed = win
					.$('[role="listbox"]:visible [role="option"]')
					.toArray()
					.map((option) => option.textContent.replace(/\s+/g, " ").trim())
					.join("\n");
				if (listed !== last) {
					last = listed;
					since = Date.now();
					return false;
				}
				return win.frappe.request.ajax_count === 0 && Date.now() - since >= 1000;
			}).then((settled) => {
				expect(settled, "the lookup's list has settled").to.be.true;
				return (last || "").split("\n").filter(Boolean);
			});
		})
		.then((listed) => {
			cy.get(first_of(selector)).clear();
			cy.get(first_of(selector)).blur();
			close_open_lists();
			settle();
			return cy.wrap(listed, { log: false });
		});
}

// The record printed in the print format, as the desk's Print shows it; the page is left again.
function print_shows(doctype, name, format, texts) {
	forget_unsaved();
	cy.visit(
		`/printview?doctype=${encodeURIComponent(doctype)}&name=${encodeURIComponent(
			name
		)}&format=${encodeURIComponent(format)}&no_letterhead=1`
	);
	cy.get(".print-format").should(($print) => {
		const text = plain($print.text());
		for (const expected of texts) expect(text, `${name} printed`).to.contain(expected);
	});
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("be.ok");
}

// The electronic signatures shown on the form (esign.js): a line signed with `meaning` that shows
// each of `texts` (the signer, the day, the act and its reason).
function signature_shows(meaning, ...texts) {
	cy.get(`${FORM} .mds-signatures`).should("exist");
	cy.get(`${FORM} .mds-signatures`)
		.last()
		.find("tbody tr")
		.should(($rows) => {
			const lines = $rows.toArray().map((row) => plain(row.textContent));
			expect(
				lines.some(
					(line) =>
						line.startsWith(meaning) && texts.every((text) => line.includes(text))
				),
				`a signature "${meaning}" showing ${texts.join(", ")} among\n${lines.join("\n")}\n`
			).to.be.true;
		});
}

// What a version of an important file keeps: the file, its fingerprint and validity, what changed,
// who added it and when (not Frappe's own bookkeeping of the row, which each save renews).
function version_as_kept(version) {
	const fields = [
		"version",
		"file",
		"file_sha256",
		"valid_until",
		"note",
		"uploaded_by",
		"uploaded_on",
	];
	return Object.fromEntries(fields.map((field) => [field, version[field] ?? null]));
}

// The text of a Text field on a table's line emptied as data on the line: a Text cell of the
// headings table does not take the cursor when clicked (found in Chrome, 2026-10-15, D9-DMR-02).
function clear_text_row(table, idx, fieldname) {
	settle();
	cy.window().then((win) => {
		const row = rows(win, table)[idx - 1];
		return win.frappe.model.set_value(row.doctype, row.name, fieldname, "");
	});
	settle();
	cy.window().should(
		(win) =>
			expect(rows(win, table)[idx - 1][fieldname], `line ${idx} ${fieldname}`).to.not.be.ok
	);
}

// A link typed in full and left: the form does not take it, and the field is left empty.
function type_link_refused(fieldname, value) {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(fieldname);
	type_and_leave(inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`), value);
	close_open_lists();
	settle();
	cy.window().should((win) =>
		expect(current(win)[fieldname], `${fieldname} not taken`).to.not.equal(value)
	);
}

// The dialogs shown, by title and text, to say what is on screen when one stays open.
function dialogs_shown() {
	return cy.window().then((win) =>
		win
			.$(".modal.show")
			.toArray()
			.map((modal) =>
				plain(
					`${win.$(modal).find(".modal-title").text()}: ${win
						.$(modal)
						.find(".modal-body")
						.text()}`
				)
			)
	);
}

// Save is pressed and goes through; the form is then left with nothing unsaved. When it is not,
// what the form changed after Save is named: each value the desk set in the form and each call that
// marked it unsaved, with where it came from (D9-FILE-02: the form shows "Not Saved" after a version
// is added, with no field differing from the record as stored).
function save_listing_unsaved() {
	cy.window().then((win) => {
		win.__sheet_changes = [];
		if (!win.frappe.model.set_value.__sheet) {
			const set_value = win.frappe.model.set_value;
			const watched = function (doctype, docname, fieldname, value, ...rest) {
				const field =
					typeof fieldname === "object" ? JSON.stringify(fieldname) : fieldname;
				win.__sheet_changes?.push(`set ${doctype}.${field} = ${JSON.stringify(value)}`);
				return set_value.apply(this, [doctype, docname, fieldname, value, ...rest]);
			};
			watched.__sheet = true;
			win.frappe.model.set_value = watched;
			const form = win.frappe.ui.form.Form.prototype;
			const dirty = form.dirty;
			form.dirty = function (...args) {
				const from = (new Error().stack || "").split("\n").slice(2, 5).join(" < ");
				win.__sheet_changes?.push(`marked unsaved from ${from}`);
				return dirty.apply(this, args);
			};
		}
	});
	primary_action().should("contain", "Save").click();
	cy.window().should((win) => expect(win.cur_frm.is_new(), "saved").to.not.be.ok);
	settle(RELOAD_DELAY_MS + QUIET_MS);
	cy.window().then((win) =>
		stored(win.cur_frm.doctype, win.cur_frm.doc.name).then((saved) => {
			const differing = Object.keys(saved)
				.filter((field) => !field.startsWith("_") && field !== "modified")
				.filter((field) => saved[field] === null || typeof saved[field] !== "object")
				.filter((field) => (win.cur_frm.doc[field] ?? null) !== (saved[field] ?? null))
				.map(
					(field) =>
						`${field}: form ${JSON.stringify(
							win.cur_frm.doc[field]
						)}, stored ${JSON.stringify(saved[field])}`
				);
			const changes = win.__sheet_changes || [];
			win.__sheet_changes = null;
			expect(
				win.cur_frm.is_dirty(),
				`nothing left unsaved after Save (differing: ${
					differing.join("; ") || "none"
				}; changes during and after Save: ${changes.join(" | ") || "none"})`
			).to.not.be.ok;
		})
	);
}

// How many signatures of `action` with `meaning` the record holds.
function signed_count(action, meaning) {
	return cy
		.window()
		.then(
			(win) =>
				(current(win).__onload?.signatures || []).filter(
					(s) => s.action === action && s.meaning === meaning
				).length
		);
}

function no_signature() {
	cy.window().should(
		(win) => expect(current(win).__onload?.signatures, "signatures").to.be.undefined
	);
	cy.get(`${FORM} .mds-signatures`).should("not.exist");
}

// None of the form's own buttons of document control is offered.
function none_offered(labels) {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	for (const label of labels) {
		cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]:visible`).should(
			"not.exist"
		);
	}
}

function no_menu(group) {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	cy.get(`${FORM} .inner-group-button[data-label="${encodeURIComponent(group)}"]`).should(
		"not.exist"
	);
}

// The signed act's refusal as the job's own request, as the form would send it: the act the form
// does not offer to the person.
function sign_refused_on_server(doctype, name, action, meaning, reason, message) {
	return refused_call(SIGNING, {
		doctype,
		name,
		action,
		password,
		reason,
		meaning,
		values: null,
	}).then((shown) => expect(shown, `${action} on ${name} is refused`).to.match(exact(message)));
}

// Looks for `check()` to come true, each look a request, until the timeout.
function poll(check, timeout = TIMEOUT) {
	const started = Date.now();
	return new Promise((resolve) => {
		const again = () =>
			check().then(
				(done) => {
					if (done) return resolve(true);
					if (Date.now() - started > timeout) return resolve(false);
					setTimeout(again, 500);
				},
				() => resolve(false)
			);
		again();
	});
}

// The desk's alert (a System Notification) with `subject` has come for the signed-in person, or,
// signed in as the Administrator, for anyone. Alerts are made in the background, so it is looked
// for until it has come.
function alerted(subject) {
	cy.window().then({ timeout: TIMEOUT + 5000 }, (win) => {
		const user = win.frappe.session.user;
		const filters = { subject: ["like", `%${subject}%`] };
		if (user !== "Administrator") filters.for_user = user;
		const look = () =>
			win.frappe
				.xcall("frappe.client.get_list", {
					doctype: "Notification Log",
					filters,
					fields: ["name"],
					limit_page_length: 1,
				})
				.then((rows) => rows.length > 0);
		return poll(look).then(
			(found) => expect(found, `${user} is alerted: ${subject}`).to.be.true
		);
	});
}

// QA fills in the impact assessment of a change under assessment and saves it.
function assess_on_form(classification, impacts = {}, days = null) {
	for (const field of IMPACTS) fill(field, impacts[field] || "No", "Select");
	fill("impact_summary", "Work instruction only; within the validated range.");
	fill("classification", classification, "Select");
	if (days !== null) fill("effectiveness_check_days", days);
	save();
}

// The process owner starts the document's next revision from the document's form: under the change
// request, with its file, what changed and the QC Inspector as reviewer; not yet saved.
function new_revision_form(document, change_request = null) {
	open_form(DOCUMENT, document);
	button("New Revision").click();
	form_is(REVISION, (doc) => doc.__islocal && doc.controlled_document === document);
	settle();
	if (change_request) {
		cy.window().should(
			(win) =>
				expect(
					win.cur_frm.get_docfield("change_request").reqd,
					"Change Request is mandatory for a document in effect"
				).to.be.ok
		);
		fill("change_request", change_request, "Link");
	}
	attach("file", "urine-bag-leak-test.pdf");
	if (change_request) fill("summary_of_change", "Leak test pressure raised to 0.4 bar");
	fill("reviewer", users[QC], "Link");
}

// The owner of an action, or QA, records it done in the change request's dialog; `refused`: the
// server's refusal, which leaves the dialog open, closed as the person closes it.
function record_action_done(note, { result = null, refused = null } = {}) {
	button("Record Action Done").click();
	open_dialog().should("contain", "Record an action as done");
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("action"), "the action").to.be.ok);
	cy.get('.modal.show textarea[data-fieldname="note"]').click().type(note);
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("note")).to.equal(note));
	if (result) {
		cy.get('.modal.show select[data-fieldname="result_doctype"]').select(result.doctype);
		cy.window()
			.its("cur_dialog")
			.should((dialog) =>
				expect(dialog.get_value("result_doctype")).to.equal(result.doctype)
			);
		choose('.modal.show input[data-fieldname="result_name"]', result.name);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("result_name")).to.equal(result.name));
	}
	if (refused) expect_refusal(refused.server);
	cy.get(".modal.show .btn-primary").contains("Record as Done").click();
	if (refused) {
		refused_with(refused.screen);
		close_dialogs();
	} else {
		cy.get(".modal.show").should("not.exist");
	}
	settle(RELOAD_DELAY_MS + QUIET_MS);
}

// The Production Supervisor's new change request on the form, listing `records` (a document each)
// with one action of their own due in a week; saved, not yet sent.
function change_request_form(records) {
	new_form(CHANGE);
	fill("title", "Raise leak test pressure (test)");
	fill("change_type", "Document", "Select");
	fill("description", "Leak test pressure from 0.3 to 0.4 bar in the work instruction.");
	fill("reason", "Two leaks found in the field at the lower pressure.");
	if (records.length) {
		rows_up_to("affected_records", records.length);
		records.forEach((record, index) => {
			fill_row("affected_records", index + 1, "reference_doctype", DOCUMENT, "Select");
			fill_row("affected_records", index + 1, "reference_name", record, "Link");
		});
		rows_up_to("actions", 1);
		fill_row("actions", 1, "description", "Issue revision 01");
		fill_row("actions", 1, "action_owner", users[PRODUCTION], "Link");
		cy.window().then((win) => fill_row("actions", 1, "due_date", in_days(win, 7), "Date"));
	}
	save();
}

function send_for_assessment(refused = null) {
	if (refused) expect_refusal(refused.server);
	button("Send for Assessment").click();
	if (refused) refused_with(refused.screen);
	settle(RELOAD_DELAY_MS + QUIET_MS);
}

// The work order of the finished good on its default bill, drawing from the approved store and
// finishing into Quarantine (standard_flow.js); saved, not yet submitted.
function work_order_form(item) {
	new_form("Work Order");
	fill("production_item", item, "Link");
	cy.window().should((win) => expect(current(win).bom_no, "the default bill").to.be.ok);
	fill("qty", 100);
	// Set after the item and its bill have loaded: each reloads the form and clears them.
	fill("source_warehouse", data.stores.approved, "Link");
	fill("wip_warehouse", data.stores.work_in_progress, "Link");
	fill("fg_warehouse", data.stores.quarantine, "Link");
	save();
}

// --- The sheet ------------------------------------------------------------------------------

context(
	"D9 Documents (documents, files, change control, device master record): the browser test sheet, as each job",
	() => {
		before(() => {
			cy.login();
			cy.visit("/desk");
			cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
				password = message.password;
				users = message.users;
			});
			cy.then(() => cy.call(`${SHEET}.prepare_sheet`, { password })).then(({ message }) => {
				data = message;
			});
		});

		beforeEach(() => {
			run.problems.length = 0;
			run.expected.length = 0;
			watch_requests();
			// Every script error is a problem of the step: the form scripts handle a refused call
			// (qa/test_form_scripts.py), so an unhandled one is a defect, never noise.
			cy.on("uncaught:exception", (error) => {
				run.problems.push(`script error: ${error?.message}`);
				return false;
			});
		});

		// --- Controlled documents and revisions -------------------------------------------------------

		it("[URS-QMS-001] [URS-QMS-002] [URS-QMS-003] D9-DOC-01 QA registers a work instruction and its process owner signs revision 00 as author", () => {
			const title = `Urine bag leak test (test) ${unique()}`;
			let document;
			let revision;
			sign_in(QA);
			new_form(DOCUMENT);
			fill("title", title);
			fill("document_type", "Work Instruction", "Select");
			fill("department", "PRD", "Select");
			fill("process_owner", users[PRODUCTION], "Link");
			save();
			cy.window().then((win) => {
				const doc = current(win);
				expect(doc.name, "numbered TYPE-DEPARTMENT-###").to.match(/^WI-PRD-\d{3,}$/);
				expect(doc.status).to.equal("Draft");
				document = doc.name;
			});
			no_problems("D9-DOC-01, QA registers the document");

			sign_in(PRODUCTION);
			cy.then(() => open_form(DOCUMENT, document));
			button("New Revision").click();
			cy.then(() =>
				form_is(REVISION, (doc) => doc.__islocal && doc.controlled_document === document)
			);
			settle();
			// The private PDF, as the attach control uploads it ("Private" left on).
			attach("file", "urine-bag-leak-test-rev00.pdf");
			fill("reviewer", users[QC], "Link");
			save();
			cy.window().then((win) => {
				const doc = current(win);
				expect(doc.name).to.equal(`${document}-REV-00`);
				expect(doc.revision).to.equal("00");
				expect(doc.status).to.equal("Draft");
				expect(doc.author, "the author").to.equal(users[PRODUCTION]);
				revision = doc.name;
			});

			// A wrong password signs nothing.
			const wrong = refusal("Incorrect password. The signature was not recorded.");
			open_signing("Sign as Author", "Author");
			cy.get(".modal.show").should("contain", "Authored");
			enter_password(`${password}-wrong`);
			expect_refusal(wrong.server);
			press_sign();
			refused_with(wrong.screen);
			close_signing();
			cy.then(() => stored(REVISION, revision)).then((doc) => {
				expect(doc.status, "nothing changes").to.equal("Draft");
				expect(doc.file_sha256, "no fingerprint").to.not.be.ok;
			});

			// Signed again, with the right password.
			sign("Sign as Author", null, "Author");
			cy.window().should((win) => {
				expect(current(win).status).to.equal("In Review");
				expect(current(win).file_sha256, "File Fingerprint (SHA-256)").to.match(
					/^[0-9a-f]{64}$/
				);
			});
			cy.window().then((win) =>
				signature_shows(
					"Authored",
					win.frappe.session.user_fullname,
					user_date(win, today(win)),
					"Author"
				)
			);
			no_problems("D9-DOC-01");
		});

		it("[URS-QMS-002] D9-DOC-02 The reviewer returns the revision with a reason, the author signs again, and the reviewer signs", () => {
			prepare("prepare_document", { password, stage: "in_review" }).then(({ revision }) => {
				const reason = "Add the pass criterion";
				let fingerprint;
				cy.then(() => stored(REVISION, revision)).then((doc) => {
					fingerprint = doc.file_sha256;
					expect(fingerprint, "authored by the process owner").to.match(
						/^[0-9a-f]{64}$/
					);
				});

				sign_in(QC);
				open_form(REVISION, revision);
				sign("Return for Changes", reason, "Return");
				cy.window().should((win) => expect(current(win).status).to.equal("Draft"));
				signed_as("Return", "Rejected");
				signature_shows("Rejected", reason);
				no_problems("D9-DOC-02, the reviewer returns it");

				sign_in(PRODUCTION);
				open_form(REVISION, revision);
				attach("file", "urine-bag-leak-test-rev00-corrected.pdf");
				save();
				sign("Sign as Author", null, "Author");
				cy.window().should((win) => {
					expect(current(win).status).to.equal("In Review");
					expect(current(win).file_sha256, "a new fingerprint")
						.to.match(/^[0-9a-f]{64}$/)
						.and.not.equal(fingerprint);
				});
				no_problems("D9-DOC-02, the author signs again");

				sign_in(QC);
				open_form(REVISION, revision);
				sign("Sign as Reviewer", null, "Review");
				cy.window().should((win) =>
					expect(current(win).status).to.equal("Awaiting Approval")
				);
				signed_as("Review", "Reviewed");
				no_problems("D9-DOC-02");
			});
		});

		it("[URS-QMS-002] [URS-QMS-004] [URS-QMS-008] D9-DOC-03 QA approves the revision; it takes effect and prints as a controlled copy", () => {
			prepare("prepare_document", { password, stage: "awaiting_approval" }).then(
				({ document, revision }) => {
					sign_in(QA);
					open_form(REVISION, revision);
					cy.window().should(
						(win) =>
							expect(current(win).effective_date, "Effective Date left empty").to.not
								.be.ok
					);
					sign("Approve");
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.status).to.equal("Effective");
						expect(doc.docstatus).to.equal(1);
						expect(doc.approver, "Approved By").to.equal(users[QA]);
						expect(doc.effective_date).to.equal(today(win));
					});
					cy.window().then((win) =>
						banner(
							"green",
							`Effective since ${user_date(
								win,
								today(win)
							)}. This is the revision in use.`
						)
					);

					open_form(DOCUMENT, document);
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.status).to.equal("Effective");
						expect(doc.current_revision, "Effective Revision").to.equal(revision);
						expect(doc.effective_since).to.equal(today(win));
						expect(doc.next_review_date, "Next Review, two years on").to.equal(
							win.frappe.datetime.add_months(today(win), 24)
						);
					});
					no_problems("D9-DOC-03, QA approves");

					print_shows(REVISION, revision, "Controlled Copy", [
						"CONTROLLED COPY",
						"Authored",
						"Reviewed",
						"Approved",
						users[PRODUCTION],
						users[QC],
						users[QA],
					]);
					no_problems("D9-DOC-03");
				}
			);
		});

		it("[URS-QMS-002] [URS-QMS-003] D9-DOC-04 The author cannot sign without a file or with themselves as reviewer", () => {
			let document;
			sign_in(QA);
			new_form(DOCUMENT);
			fill("title", `Urine bag drain valve check (test) ${unique()}`);
			fill("document_type", "Work Instruction", "Select");
			fill("department", "PRD", "Select");
			fill("process_owner", users[QA], "Link");
			save();
			cy.window().then((win) => {
				document = current(win).name;
			});
			button("New Revision").click();
			cy.then(() =>
				form_is(REVISION, (doc) => doc.__islocal && doc.controlled_document === document)
			);
			settle();
			save();
			cy.window().should((win) => {
				expect(current(win).status).to.equal("Draft");
				expect(current(win).author).to.equal(users[QA]);
			});

			sign_refused(
				"Sign as Author",
				null,
				refusal("Attach the document file before signing."),
				"Author"
			);

			attach("file", "urine-bag-drain-valve-check.pdf");
			fill("reviewer", users[QA], "Link");
			save();
			sign_refused(
				"Sign as Author",
				null,
				refusal("The reviewer cannot be the author."),
				"Author"
			);

			cy.window().then((win) => open_form(REVISION, current(win).name));
			cy.window().should((win) =>
				expect(current(win).status, "still a draft").to.equal("Draft")
			);
			no_signature();
			no_problems("D9-DOC-04");
		});

		it("[URS-QMS-014] [URS-QMS-005] D9-DOC-05 A second revision needs an approved change request; Sales sees only the effective revision", () => {
			prepare("prepare_revision_rules", { password }).then((rules) => {
				sign_in(PRODUCTION);
				open_form(DOCUMENT, rules.document);
				button("New Revision").click();
				form_is(
					REVISION,
					(doc) => doc.__islocal && doc.controlled_document === rules.document
				);
				settle();
				cy.window().should(
					(win) =>
						expect(
							win.cur_frm.get_docfield("change_request").reqd,
							"Change Request is mandatory"
						).to.be.ok
				);
				// The form's own mandatory check: nothing is sent.
				primary_action().should("contain", "Save").click();
				refused_with(
					/Missing Fields: Please fill the following mandatory fields before saving:\s*Change Request is required\./
				);
				cy.window().should((win) => expect(win.cur_frm.is_new(), "not saved").to.be.ok);
				// The server's own check, as an API call without the form.
				refused_call("frappe.client.insert", {
					doc: { doctype: REVISION, controlled_document: rules.document },
				}).then((message) =>
					expect(message).to.match(
						exact(
							`${rules.document} is in effect: name the approved change request for this revision.`
						)
					)
				);

				// The lookup lists the approved request that lists the document, not one that does not.
				lookup_offers("change_request", "CR-").then((listed) => {
					expect(
						listed.some((entry) => entry.includes(rules.listing_change)),
						`${rules.listing_change} offered in\n${listed.join("\n")}\n`
					).to.be.true;
					expect(
						listed.some((entry) => entry.includes(rules.other_change)),
						`${rules.other_change}, which lists another document, offered`
					).to.be.false;
				});
				no_problems("D9-DOC-05, the Production Supervisor");

				sign_in(SALES);
				open_list(REVISION);
				cy.window().should((win) =>
					expect(
						win.cur_list.data.map((row) => row.name),
						"another person's draft"
					).to.not.include(rules.draft)
				);
				cy.call("frappe.client.get_list", {
					doctype: REVISION,
					fields: ["name", "status"],
					limit_page_length: 0,
				}).then(({ message }) => {
					expect(
						message.filter((row) => row.status !== "Effective").map((row) => row.name),
						"revisions Sales sees that are not Effective"
					).to.be.empty;
					expect(message.map((row) => row.name)).to.include(rules.revision);
					expect(message.map((row) => row.name)).to.not.include(rules.draft);
				});
				open_list(REVISION, { name: rules.draft });
				cy.window().should((win) => expect(win.cur_list.data).to.have.length(0));
				no_problems("D9-DOC-05");
			});
		});

		it("[URS-QMS-006] D9-DOC-06 QA withdraws a document with a signed reason", () => {
			const reason = "Replaced by the merged line WI";
			prepare("prepare_document", { password, stage: "effective" }).then((free) =>
				prepare("prepare_document", { password, stage: "effective", in_progress: 1 }).then(
					(busy) => {
						sign_in(QA);
						open_form(DOCUMENT, free.document);
						sign("Withdraw", reason, "Withdraw", () =>
							from_menu("Actions", "Withdraw")
						);
						cy.window().should((win) => {
							const doc = current(win);
							expect(doc.status).to.equal("Obsolete");
							expect(doc.withdrawn_reason).to.equal(reason);
							expect(doc.next_review_date, "Next Review").to.not.be.ok;
						});
						banner("red", `Withdrawn: ${reason}`);
						none_offered([
							"New Revision",
							"Effective Revision",
							"Revision in Progress",
							"Review: Still Current",
						]);
						no_menu("Actions");
						signature_shows("Withdrawn", reason);

						open_form(REVISION, free.revision);
						cy.window().should((win) =>
							expect(current(win).status).to.equal("Obsolete")
						);
						banner(
							"red",
							"The document has been withdrawn. Do not use this revision."
						);
						print_shows(REVISION, free.revision, "Controlled Copy", [
							"UNCONTROLLED – NOT FOR USE",
						]);

						// A document with a revision in progress is not withdrawn.
						open_form(DOCUMENT, busy.document);
						offered("Revision in Progress", true);
						no_menu("Actions");
						sign_refused_on_server(
							DOCUMENT,
							busy.document,
							"Withdraw",
							"Withdrawn",
							reason,
							`Finish or return the revision in progress before withdrawing ${busy.document}.`
						);
						no_problems("D9-DOC-06");
					}
				)
			);
		});

		// --- The important files repository --------------------------------------------------------------

		it("[URS-PLT-018] D9-FILE-01 QA sets up a group and a sub-group; a group cannot hold files", () => {
			const tag = unique();
			const group = `Licences (test) ${tag}`;
			const sub_group = `CDSCO (test) ${tag}`;
			sign_in(QA);
			new_form("File Category");
			fill("category_name", group);
			fill("is_group", 1, "Check");
			fill_jobs("readers", "Stock User");
			fill_jobs("editors", "Purchase User");
			save();
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.name).to.equal(group);
				expect(doc.is_group, "Holds Sub-groups").to.equal(1);
				expect(doc.readers.map((row) => row.role)).to.deep.equal(["Stock User"]);
				expect(doc.editors.map((row) => row.role)).to.deep.equal(["Purchase User"]);
			});

			new_form("File Category");
			fill("category_name", sub_group);
			fill("parent_file_category", group, "Link");
			save();
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.name).to.equal(sub_group);
				expect(doc.parent_file_category, "Within").to.equal(group);
				// No jobs of its own: it takes its group's (proved by D9-FILE-02 and 03).
				expect(doc.readers || []).to.be.empty;
				expect(doc.editors || []).to.be.empty;
			});
			no_problems("D9-FILE-01, the categories");

			new_form(FILE);
			fill("title", `Licence copy (test) ${tag}`);
			fill("category", group, "Link");
			attach("new_file", "licence-copy.pdf");
			save_refused(refusal(`Files are kept in a category, not in the group ${group}.`));
			cy.window().should((win) => expect(win.cur_frm.is_new(), "not saved").to.be.ok);
			no_problems("D9-FILE-01");
		});

		it("[URS-PLT-019] D9-FILE-02 Purchase uploads a licence copy and a second version", () => {
			prepare("prepare_file_categories").then(({ sub_group }) => {
				const title = `MD-9 licence (test) ${unique()}`;
				let first;
				sign_in(PURCHASE);
				new_form(FILE);
				fill("title", title);
				fill("category", sub_group, "Link");
				attach("new_file", "md-9-licence.pdf");
				cy.window().then((win) => fill("new_valid_until", in_days(win, 30), "Date"));
				save_listing_unsaved();
				cy.window().should((win) => {
					const doc = current(win);
					expect(doc.status).to.equal("Active");
					expect(doc.current_version, "Current Version").to.equal(1);
					expect(doc.valid_until, "Valid Until").to.equal(in_days(win, 30));
					expect(doc.versions).to.have.length(1);
					const version = doc.versions[0];
					expect(version.version).to.equal(1);
					expect(version.file_sha256, "its fingerprint").to.match(/^[0-9a-f]{64}$/);
					expect(version.note).to.equal("First version");
					expect(version.uploaded_by).to.equal(users[PURCHASE]);
					expect(version.uploaded_on.slice(0, 10)).to.equal(today(win));
				});
				cy.window().then((win) => {
					first = version_as_kept(current(win).versions[0]);
				});

				attach("new_file", "md-9-licence-renewed.pdf");
				save_refused(refusal("Say what changed in this version."));

				fill("new_note", "Renewed for five years");
				save_listing_unsaved();
				cy.window().should((win) => {
					const doc = current(win);
					expect(doc.current_version).to.equal(2);
					expect(doc.versions).to.have.length(2);
					expect(
						version_as_kept(doc.versions[0]),
						"version 1 is unchanged"
					).to.deep.equal(first);
					expect(doc.versions[1].version).to.equal(2);
					expect(doc.versions[1].note).to.equal("Renewed for five years");
					expect(doc.versions[1].file_sha256).to.match(/^[0-9a-f]{64}$/);
					for (const field of ["new_file", "new_valid_until", "new_note"]) {
						expect(doc[field], `${field} is cleared`).to.not.be.ok;
					}
				});
				no_problems("D9-FILE-02, two versions");

				// A file uploaded as public is refused.
				attach("new_file", "md-9-licence-public.pdf", { is_private: false });
				fill("new_note", "A public copy");
				save_refused(refusal("Attach the document as a private file."));
				cy.window().then((win) =>
					stored(FILE, current(win).name).then((doc) =>
						expect(doc.current_version, "no version added").to.equal(2)
					)
				);
				no_problems("D9-FILE-02");
			});
		});

		it("[URS-PLT-018] [URS-PLT-019] D9-FILE-03 Who sees the file, and archiving it", () => {
			prepare("prepare_file_categories", { with_file: 1 }).then(({ file, title }) => {
				sign_in(SALES);
				open_list(FILE, { title });
				cy.window().should(
					(win) => expect(win.cur_list.data, "files Sales sees").to.be.empty
				);
				cy.call("frappe.client.get_count", {
					doctype: FILE,
					filters: { name: file },
				}).then(({ message }) => expect(message, "Sales sees the file").to.equal(0));
				no_problems("D9-FILE-03, Sales");

				sign_in(STORES);
				open_list(FILE, { title });
				cy.window().should((win) =>
					expect(win.cur_list.data.map((row) => row.name)).to.deep.equal([file])
				);
				open_form(FILE, file);
				fill("title", `${title} (changed)`);
				expect_refusal(
					/PermissionError.*You need the 'write' permission on Important File/
				);
				primary_action().should("contain", "Save").click();
				refused_with(
					exact(
						`You need the 'write' permission on Important File ${file} to perform this action.`
					)
				);
				settle();
				cy.then(() => stored(FILE, file)).then((doc) =>
					expect(doc.title, "the title is unchanged").to.equal(title)
				);
				no_problems("D9-FILE-03, Stores");

				const why = "Test complete";
				sign_in(PURCHASE);
				open_form(FILE, file);
				button("Archive").click();
				open_dialog().should("contain", `Archive ${title}`);
				cy.get('.modal.show textarea[data-fieldname="reason"]').click().type(why);
				cy.window()
					.its("cur_dialog")
					.should((dialog) => expect(dialog.get_value("reason")).to.equal(why));
				cy.get(".modal.show .btn-primary").click();
				settle(RELOAD_DELAY_MS + QUIET_MS);
				dialogs_shown().then((shown) =>
					expect(shown.join("\n"), "dialogs left on screen after Archive").to.equal("")
				);
				cy.get(".modal.show").should("not.exist");
				cy.window().should((win) => {
					expect(current(win).status).to.equal("Archived");
					expect(current(win).archived_reason, "Archived Because").to.equal(why);
				});

				attach("new_file", "md-9-licence-after-archiving.pdf");
				fill("new_note", "After archiving");
				save_refused(refusal(`${file} is archived.`));
				cy.then(() => stored(FILE, file)).then((doc) => {
					expect(doc.status).to.equal("Archived");
					expect(doc.current_version, "no version added").to.equal(1);
				});
				no_problems("D9-FILE-03");
			});
		});

		// --- Change control ---------------------------------------------------------------------------

		it("[URS-QMS-009] D9-CC-01 The Production Supervisor raises a change request and sends it for assessment", () => {
			prepare("prepare_document", { password, stage: "effective" }).then(({ document }) => {
				let change;
				sign_in(PRODUCTION);
				change_request_form([]);
				cy.window().then((win) => {
					const doc = current(win);
					const year = today(win).slice(0, 4);
					expect(doc.name).to.match(new RegExp(`^CR-${year}-\\d{4,}$`));
					expect(doc.status).to.equal("Draft");
					expect(doc.requested_by, "Raised By").to.equal(users[PRODUCTION]);
					change = doc.name;
				});

				send_for_assessment(refusal("List the records the change affects."));
				cy.then(() => stored(CHANGE, change)).then((doc) =>
					expect(doc.status, "still a draft").to.equal("Draft")
				);

				rows_up_to("affected_records", 1);
				fill_row("affected_records", 1, "reference_doctype", DOCUMENT, "Select");
				fill_row("affected_records", 1, "reference_name", document, "Link");
				rows_up_to("actions", 1);
				fill_row("actions", 1, "description", "Issue revision 01");
				fill_row("actions", 1, "action_owner", users[PRODUCTION], "Link");
				cy.window().then((win) =>
					fill_row("actions", 1, "due_date", in_days(win, 7), "Date")
				);
				save();
				send_for_assessment();
				cy.window().should((win) =>
					expect(current(win).status).to.equal("Under Assessment")
				);
				banner("blue", "Waiting for QA to assess its impact.");
				no_problems("D9-CC-01, the Production Supervisor");

				sign_in(QA);
				cy.then(() => alerted(`Change request ${change} is waiting for your assessment`));
				no_problems("D9-CC-01");
			});
		});

		it("[URS-QMS-010] [URS-QMS-024] [URS-REG-006] D9-CC-02 QA's assessment is refused until complete, then signed", () => {
			prepare("prepare_changes", { password, stages: ["under_assessment"] }).then(
				({ changes: [change] }) => {
					sign_in(QA);
					open_form(CHANGE, change);
					sign_refused(
						"Assess",
						null,
						refusal(
							"Record the impact on: Product Quality, Risk (ISO 14971), Regulatory Notification (CDSCO), Validation, Training, Documents, Stock in Hand and Work in Progress."
						)
					);

					assess_on_form("Minor", { impact_regulatory: "Yes" });
					sign_refused(
						"Assess",
						null,
						refusal("A change that needs regulatory notification is a Major change.")
					);

					fill("impact_regulatory", "No", "Select");
					fill("impact_risk", "Yes", "Select");
					save();
					sign_refused(
						"Assess",
						null,
						refusal(
							"The change affects risk: list the Risk Management File it affects."
						)
					);

					fill("impact_risk", "No", "Select");
					// Checked from the day it is implemented, so that it can be closed the same day.
					fill("effectiveness_check_days", 0);
					save();
					sign("Assess");
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.status).to.equal("Awaiting Approval");
						expect(doc.assessed_by, "Assessed By").to.equal(users[QA]);
					});
					signed_as("Assess", "Reviewed");
					banner("orange", "Assessed as a Minor change; waiting for QA approval.");
					no_problems("D9-CC-02");
				}
			);
		});

		it("[URS-QMS-011] [URS-QMS-012] [URS-QMS-013] [URS-QMS-014] D9-CC-03 QA approves a Minor change; the owner completes the action; QA verifies it effective", () => {
			prepare("prepare_changes", { password, stages: ["awaiting_approval"] }).then(
				({ document, revision: first, changes: [change] }) => {
					let revision;
					sign_in(QA);
					open_form(CHANGE, change);
					sign("Approve");
					cy.window().should((win) => {
						expect(current(win).status).to.equal("Approved");
						expect(current(win).qa_approver, "Approved by QA").to.equal(users[QA]);
					});
					signed_as("Approve", "Approved");
					no_problems("D9-CC-03, QA approves");

					// The next revision under the change, through author, review and approval.
					sign_in(PRODUCTION);
					alerted(`${change} was approved`);
					new_revision_form(document, change);
					save();
					cy.window().then((win) => {
						revision = current(win).name;
						expect(revision).to.equal(`${document}-REV-01`);
					});
					sign("Sign as Author", null, "Author");
					no_problems("D9-CC-03, the author signs");
					sign_in(QC);
					cy.then(() => open_form(REVISION, revision));
					sign("Sign as Reviewer", null, "Review");
					no_problems("D9-CC-03, the reviewer signs");
					sign_in(QA);
					cy.then(() => open_form(REVISION, revision));
					sign("Approve");
					cy.window().should((win) => expect(current(win).status).to.equal("Effective"));
					cy.then(() => stored(REVISION, first)).then((doc) => {
						expect(doc.status, "revision 00").to.equal("Superseded");
						expect(doc.superseded_by).to.equal(revision);
					});
					open_form(CHANGE, change);
					cy.window().should((win) => {
						const row = current(win).affected_records[0];
						expect(row.change_record_type).to.equal(REVISION);
						expect(row.change_record, "Changed In").to.equal(revision);
						expect(row.changed_on, "Changed On").to.be.ok;
					});
					no_problems("D9-CC-03, the revision takes effect");

					sign_in(PRODUCTION);
					open_form(CHANGE, change);
					cy.then(() =>
						record_action_done("Rev 01 effective", {
							result: { doctype: REVISION, name: revision },
						})
					);
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.status, "the revision is in force").to.equal("Implemented");
						expect(doc.implemented_on).to.equal(today(win));
						expect(doc.effectiveness_due_on, "Effectiveness Check Due").to.equal(
							today(win)
						);
						expect(doc.actions[0].result_name).to.equal(revision);
					});
					no_problems("D9-CC-03, the action is done");

					const reason = "Leak test results checked";
					sign_in(QA);
					open_form(CHANGE, change);
					sign("Verify: Effective", reason, "Verify Effective");
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.status).to.equal("Closed");
						expect(doc.effectiveness_outcome).to.equal("Effective");
						expect(doc.effectiveness_note).to.equal(reason);
					});
					signed_as("Verify Effective", "Verified");
					no_problems("D9-CC-03");
				}
			);
		});

		it("[URS-QMS-011] [URS-QMS-015] D9-CC-04 A Major change is approved by QA and then by Management", () => {
			prepare("prepare_changes", { password, stages: ["under_assessment"] }).then(
				({ changes: [change] }) => {
					sign_in(QA);
					open_form(CHANGE, change);
					assess_on_form("Major");
					sign("Assess");
					cy.window().should((win) =>
						expect(current(win).status).to.equal("Awaiting Approval")
					);
					sign("Approve");
					cy.window().should((win) => {
						expect(current(win).status).to.equal("Awaiting Management Approval");
						expect(current(win).qa_approver).to.equal(users[QA]);
					});
					banner(
						"orange",
						"A Major change approved by QA; waiting for Management approval."
					);
					offered("Approve", false);
					sign_refused_on_server(
						CHANGE,
						change,
						"Approve",
						"Approved",
						null,
						"Only Management can approve a Major change request."
					);
					no_problems("D9-CC-04, QA approves");

					sign_in(MANAGEMENT);
					alerted(`Major change request ${change} is waiting for Management approval`);
					open_form(CHANGE, change);
					sign("Approve");
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.status).to.equal("Approved");
						expect(doc.qa_approver, "Approved by QA").to.equal(users[QA]);
						expect(doc.management_approver, "Approved by Management").to.equal(
							users[MANAGEMENT]
						);
					});
					signed_count("Approve", "Approved").should("equal", 2);
					no_problems("D9-CC-04");
				}
			);
		});

		it("[URS-QMS-011] [URS-QMS-016] D9-CC-05 QA returns one change, rejects another, and the raiser withdraws a third", () => {
			prepare("prepare_changes", {
				password,
				stages: ["awaiting_approval", "under_assessment", "under_assessment", "approved"],
			}).then(({ changes: [returned, rejected, withdrawn, approved] }) => {
				sign_in(QA);
				open_form(CHANGE, returned);
				cy.window().should((win) => expect(current(win).assessed_by).to.equal(users[QA]));
				sign("Return for Changes", "Add the stock assessment", "Return");
				cy.window().should((win) => {
					expect(current(win).status).to.equal("Draft");
					expect(current(win).assessed_by, "Assessed By is cleared").to.not.be.ok;
				});
				banner("orange", "Returned: Add the stock assessment");
				signature_shows("Rejected", "Add the stock assessment");

				open_form(CHANGE, rejected);
				sign("Reject", "Not justified");
				cy.window().should((win) => {
					expect(current(win).status).to.equal("Rejected");
					expect(current(win).decision_reason).to.equal("Not justified");
				});
				banner("red", "Rejected: Not justified");
				signature_shows("Rejected", "Not justified");
				no_problems("D9-CC-05, QA decides");

				const why = "Superseded by the new leak tester";
				sign_in(PRODUCTION);
				open_form(CHANGE, withdrawn);
				from_menu("Actions", "Withdraw");
				open_dialog().should("contain", `Withdraw ${withdrawn}`);
				cy.get('.modal.show textarea[data-fieldname="reason"]').click().type(why);
				cy.window()
					.its("cur_dialog")
					.should((dialog) => expect(dialog.get_value("reason")).to.equal(why));
				cy.get(".modal.show .btn-primary").contains("Withdraw").click();
				cy.get(".modal.show").should("not.exist");
				settle(RELOAD_DELAY_MS + QUIET_MS);
				cy.window().should((win) => {
					expect(current(win).status).to.equal("Withdrawn");
					expect(current(win).decision_reason).to.equal(why);
				});
				// change_request.js asks for "gray", which Frappe's form message does not have: it draws it blue.
				banner("blue", `Withdrawn: ${why}`);
				no_signature();

				open_form(CHANGE, approved);
				no_menu("Actions");
				refused_call(`${CHANGE_CONTROL}.withdraw`, { name: approved, reason: why }).then(
					(message) =>
						expect(message).to.match(
							exact(`${approved} is Approved; it can no longer be withdrawn.`)
						)
				);
				no_problems("D9-CC-05");
			});
		});

		it("[URS-QMS-013] [URS-QMS-024] D9-CC-06 Not Effective is recorded only on or after the check date and with risk reviews closed", () => {
			prepare("prepare_risk_change", { password }).then((risk) => {
				sign_in(QA);
				stored("Risk Review", risk.review).then((review) => {
					expect(review.trigger, "Raised By").to.equal("Change Request");
					expect(review.source_name).to.equal(risk.change_request);
					expect(review.risk_management_file).to.equal(risk.file);
					expect(review.status).to.equal("Open");
				});
				open_form(CHANGE, risk.change_request);
				cy.window().should((win) => expect(current(win).status).to.equal("Implemented"));
				sign_refused(
					"Verify: Not Effective",
					"Leaks continued",
					refusal(
						`Risk review ${risk.review} raised by this change is open; QA closes it first.`
					),
					"Verify Not Effective"
				);

				open_form("Risk Review", risk.review);
				sign(
					"Close: No Change",
					"Leak rate within the estimate of the risk assessment",
					"Close: No Change"
				);
				cy.window().should((win) => expect(current(win).status).to.equal("Closed"));

				open_form(CHANGE, risk.change_request);
				sign("Verify: Not Effective", "Leaks continued", "Verify Not Effective");
				cy.window().should((win) => {
					expect(current(win).status).to.equal("Closed");
					expect(current(win).effectiveness_outcome).to.equal("Not effective");
				});
				signed_as("Verify Not Effective", "Verified");
				no_problems("D9-CC-06, with the review closed");

				// Its check date in the future: no Verify is offered, and the server refuses it.
				open_form(CHANGE, risk.later_change);
				none_offered(["Verify: Effective", "Verify: Not Effective"]);
				cy.window().then((win) =>
					sign_refused_on_server(
						CHANGE,
						risk.later_change,
						"Verify Not Effective",
						"Verified",
						"Leaks continued",
						`The effectiveness of ${
							risk.later_change
						} is checked on or after ${user_date(win, risk.later_due)}.`
					)
				);
				no_problems("D9-CC-06");
			});
		});

		// --- The device master record ----------------------------------------------------------------

		it("[URS-QMS-026] [URS-QMS-027] [URS-QMS-028] [URS-QMS-030] D9-DMR-01 The Production Supervisor authors the next revision of a device master record and QA approves it", () => {
			prepare("prepare_dmr", { password }).then(({ item, record }) => {
				let revision;
				sign_in(PRODUCTION);
				open_form(DMR, record);
				banner("green", "Current: the record this device is made by.");
				button("Start New Revision").click();
				form_is(DMR, (doc) => doc.name !== record && doc.status === "Draft");
				settle();
				stored(DMR, record).then((previous) =>
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.name).to.equal(
							`DMR-${item}-${two_digits(Number(previous.revision) + 1)}`
						);
						expect(doc.supersedes).to.equal(record);
						expect(
							doc.headings.map((row) => [row.heading, row.content]),
							"the headings of the previous revision"
						).to.deep.equal(
							previous.headings.map((row) => [row.heading, row.content])
						);
						expect(
							doc.documents.map((row) => row.controlled_document),
							"its documents"
						).to.deep.equal(previous.documents.map((row) => row.controlled_document));
						expect(doc.elements, "the elements").to.not.be.empty;
						for (const row of doc.elements) {
							expect(row.pinned_version, `${row.reference_name} pinned`).to.be.ok;
						}
					})
				);
				fill("summary_of_change", "IFU wording checked against the current labelling");
				save();
				cy.window().then((win) => {
					revision = current(win).name;
				});
				sign("Sign as Author", null, "Author");
				cy.window().should((win) =>
					expect(current(win).status).to.equal("Awaiting Approval")
				);
				signed_as("Author", "Authored");
				no_problems("D9-DMR-01, the author signs");

				sign_in(QA);
				cy.then(() => open_form(DMR, revision));
				sign("Approve");
				cy.window().should((win) => {
					expect(current(win).status).to.equal("Current");
					expect(current(win).docstatus).to.equal(1);
				});
				banner("green", "Current: the record this device is made by.");
				open_form(DMR, record);
				cy.window().should((win) => expect(current(win).status).to.equal("Superseded"));
				cy.then(() => banner("red", `Superseded by ${revision}; not for use.`));
				no_problems("D9-DMR-01, QA approves");

				cy.then(() =>
					print_shows(DMR, revision, "Device Master Record", ["CONTROLLED INDEX"])
				);
				print_shows(DMR, record, "Device Master Record", [
					"NOT FOR USE · Status: Superseded",
				]);
				no_problems("D9-DMR-01");
			});
		});

		it("[URS-QMS-026] [URS-QMS-027] D9-DMR-02 An incomplete device master record cannot be signed, and only one revision may be in progress", () => {
			prepare("prepare_dmr", { password }).then(({ item, record }) => {
				let revision;
				sign_in(PRODUCTION);
				open_form(DMR, record);
				button("Start New Revision").click();
				form_is(DMR, (doc) => doc.name !== record && doc.status === "Draft");
				settle();
				cy.window().then((win) => {
					revision = current(win).name;
					if (current(win).summary_of_change) clear_field("summary_of_change");
					const idx =
						current(win).headings.findIndex((row) => row.heading === MEASURING) + 1;
					expect(idx, `the heading "${MEASURING}"`).to.be.above(0);
					clear_text_row("headings", idx, "content");
				});
				save();
				cy.window().should((win) => {
					const row = current(win).headings.find((each) => each.heading === MEASURING);
					expect(row.content || "", "the heading's content").to.equal("");
					expect(current(win).summary_of_change, "What Changed").to.not.be.ok;
				});
				sign_refused(
					"Sign as Author",
					null,
					refusal("Say what changed in this revision."),
					"Author"
				);

				fill("summary_of_change", "Measuring and monitoring to be rewritten");
				save();
				cy.then(() =>
					sign_refused(
						"Sign as Author",
						null,
						refusal(
							`${revision} cannot be signed yet; it lacks the heading "${MEASURING}": describe it, or say why it does not apply.`
						),
						"Author"
					)
				);

				new_form(DMR);
				fill("item", item, "Link");
				cy.then(() =>
					save_refused(
						refusal(`${item} already has a revision in progress: ${revision}.`)
					)
				);
				no_problems("D9-DMR-02");
			});
		});

		it("[URS-QMS-028] D9-DMR-03 QA returns a revision for changes and withdraws a draft", () => {
			prepare("prepare_dmr", { password, stage: "authored" }).then(
				({ item, record, revision }) => {
					sign_in(QA);
					open_form(DMR, revision);
					cy.window().should((win) =>
						expect(current(win).status).to.equal("Awaiting Approval")
					);
					sign("Return for Changes", "Name the IFU leaflet", "Return");
					cy.window().should((win) => expect(current(win).status).to.equal("Draft"));
					signature_shows("Rejected", "Name the IFU leaflet");

					sign("Withdraw", "Not needed after all");
					cy.window().should((win) => expect(current(win).status).to.equal("Withdrawn"));
					banner("red", "Withdrawn; not for use.");
					cy.call("frappe.client.get_value", {
						doctype: DMR,
						filters: { item, status: "Current" },
						fieldname: "name",
					}).then(({ message }) =>
						expect(message?.name, "the item's Current revision").to.equal(record)
					);
					no_problems("D9-DMR-03");
				}
			);
		});

		it("[URS-QMS-029] [URS-QMS-031] D9-DMR-04 A change to a pinned document puts the record Out of Date and blocks the work order", () => {
			prepare("prepare_dmr", { password }).then(({ item, record }) =>
				prepare("issue_pinned_document_revision", { password }).then((issued) => {
					let order;
					expect(issued.record, "the record that pins the document").to.equal(record);
					const changed = `Controlled Document ${issued.document}: now Revision ${issued.revision_number}`;
					// QA is told; the QA who approved the revision does not alert themselves (Frappe), so
					// the alert is looked for among QA's people, as the Administrator.
					alerted(`Device master record ${record} of ${item} is out of date`);

					sign_in(QA);
					open_form(DMR, record);
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.status).to.equal("Out of Date");
						const row = doc.elements.find(
							(each) =>
								each.reference_doctype === "Controlled Document" &&
								each.reference_name === issued.document
						);
						expect(row?.stale, "Changed Since Approval").to.equal(1);
					});
					cy.window().then((win) =>
						banner(
							"red",
							`Out of date since ${win.frappe.datetime.str_to_user(
								current(win).out_of_date_since
							)}; not for use until QA approves a new revision.`
						)
					);
					banner("red", changed);
					// The item's open work orders, as the server lists them on the form (an earlier run's
					// order submitted in this test stays open), or "none".
					cy.window().then((win) =>
						banner(
							"red",
							`Open work orders: ${
								(current(win).__onload?.open_work_orders || []).join(", ") ||
								"none"
							}`
						)
					);
					no_problems("D9-DMR-04, QA sees it out of date");

					sign_in(PRODUCTION);
					work_order_form(item);
					cy.window().then((win) => {
						order = current(win).name;
					});
					submit_refused(
						refusal(
							`${item} has no Current device master record; QA approves it before ${item} is put into production.`
						)
					);
					no_problems("D9-DMR-04, the work order is refused");

					// After QA approves a new revision (as D9-DMR-01), the work order is submitted.
					prepare("prepare_dmr", { password }).then(({ record: current_record }) => {
						sign_in(PRODUCTION);
						open_form("Work Order", order);
						submit();
						cy.then(() => stored("Work Order", order)).then((doc) =>
							expect(doc.mds_device_master_record, "released on").to.equal(
								current_record
							)
						);
						no_problems("D9-DMR-04");
					});
				})
			);
		});

		// --- Added to the sheet since: the periodic review, implemented in force, listed once -----------

		it("[URS-QMS-034] D9-DOC-07 QA signs the periodic review: still current, next review moved on", () => {
			prepare("prepare_review_due", { password }).then((due) => {
				const reason = "Read against current practice; still correct";
				let revision;
				sign_in(PRODUCTION);
				open_form(DOCUMENT, due.document);
				offered("New Revision", true);
				offered("Review: Still Current", false);
				no_problems("D9-DOC-07, its process owner");

				sign_in(QA);
				open_form(DOCUMENT, due.document);
				cy.window().then((win) =>
					banner("orange", `Periodic review due on ${user_date(win, due.next_review)}.`)
				);
				button("Review: Still Current").should("have.class", "btn-primary");

				// Without a reason the dialog does not sign; nor does the server.
				open_signing("Review: Still Current", "Review");
				enter_password();
				press_sign();
				refused_with(
					/Missing Values Required: Following fields have missing values:\s*Reason/
				);
				cy.window().should((win) =>
					expect(win.cur_dialog?.title, "the signing dialog stays open").to.equal(
						"Sign: Review"
					)
				);
				close_signing();
				sign_refused_on_server(
					DOCUMENT,
					due.document,
					"Review",
					"Reviewed",
					"",
					"Say what the review found."
				);

				sign("Review: Still Current", reason, "Review");
				cy.window().should((win) => {
					const doc = current(win);
					expect(doc.next_review_date, "two years (Review Every) from today").to.equal(
						win.frappe.datetime.add_months(today(win), 12 * doc.review_period_years)
					);
					expect(doc.status).to.equal("Effective");
					expect(doc.current_revision, "Effective Revision unchanged").to.equal(
						due.revision
					);
				});
				signature_shows("Reviewed", "Review", reason);
				cy.contains(`${FORM} .form-message`, "Periodic review due").should("not.exist");

				open_form("Reminder", due.reminder);
				cy.window().should((win) => {
					const doc = current(win);
					expect(doc.category).to.equal("Document Review");
					expect(doc.reference_name).to.equal(due.document);
					expect(doc.status).to.equal("Completed");
					expect(doc.completed_by, "Completed By").to.equal(users[QA]);
					expect(doc.completion).to.equal("Reviewed, still current: revision 00");
				});
				no_problems("D9-DOC-07, QA reviews it");

				sign_in(PRODUCTION);
				new_revision_form(due.document, due.change_request);
				save();
				cy.window().then((win) => {
					revision = current(win).name;
				});
				no_problems("D9-DOC-07, the next revision is started");

				sign_in(QA);
				open_form(DOCUMENT, due.document);
				offered("Review: Still Current", false);
				offered("Revision in Progress", true);
				cy.then(() =>
					sign_refused_on_server(
						DOCUMENT,
						due.document,
						"Review",
						"Reviewed",
						reason,
						`Revision ${revision} of ${due.document} is in progress; the review is settled when it takes effect.`
					)
				);
				no_problems("D9-DOC-07");
			});
		});

		it("[URS-QMS-032] D9-CC-07 A change is implemented only once what it changed is in force", () => {
			prepare("prepare_changes", { password, stages: ["approved"] }).then(
				({ document, changes: [change] }) => {
					let revision;
					sign_in(PRODUCTION);
					new_revision_form(document, change);
					save();
					cy.window().then((win) => {
						revision = current(win).name;
					});
					sign("Sign as Author", null, "Author");
					cy.then(() => stored(CHANGE, change)).then((doc) =>
						expect(doc.affected_records[0].change_record, "Changed In").to.equal(
							revision
						)
					);
					no_problems("D9-CC-07, the author signs");

					sign_in(QC);
					cy.then(() => open_form(REVISION, revision));
					sign("Sign as Reviewer", null, "Review");
					no_problems("D9-CC-07, the reviewer signs");

					sign_in(QA);
					cy.then(() => open_form(REVISION, revision));
					cy.window().then((win) => fill("effective_date", in_days(win, 10), "Date"));
					save();
					sign("Approve");
					cy.window().should((win) => {
						expect(current(win).status, "approved, not yet effective").to.equal(
							"Approved"
						);
						expect(current(win).effective_date).to.equal(in_days(win, 10));
					});
					cy.window().then((win) =>
						banner(
							"blue",
							`Approved; takes effect on ${user_date(win, in_days(win, 10))}.`
						)
					);
					no_problems("D9-CC-07, QA approves for later");

					sign_in(PRODUCTION);
					open_form(CHANGE, change);
					cy.then(() =>
						record_action_done("Revision issued; it takes effect in ten days", {
							refused: refusal(
								`${change} is implemented once what it changed is in force. Not yet in force: Document Revision ${revision} (Approved). Record action 1 as done once it is.`
							),
						})
					);
					cy.then(() => stored(CHANGE, change)).then((doc) => {
						expect(doc.status).to.equal("Approved");
						expect(doc.actions[0].done, "the action stays undone").to.equal(0);
					});
					no_problems("D9-CC-07, refused before it is in force");

					// Its effective date has come: the daily run makes it effective (qa/sheets/d9a.py).
					cy.then(() => prepare("take_effect_now", { revision })).then((status) =>
						expect(status).to.equal("Effective")
					);
					sign_in(PRODUCTION);
					open_form(CHANGE, change);
					record_action_done("Revision in force");
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.status).to.equal("Implemented");
						expect(doc.implemented_on, "Implemented On").to.equal(today(win));
						expect(doc.effectiveness_due_on, "Effectiveness Check Due").to.be.ok;
					});
					no_problems("D9-CC-07");
				}
			);
		});

		it("[URS-QMS-033] D9-CC-08 An approved request changes each listed record once; a record is listed once", () => {
			prepare("prepare_spent_change", { password }).then(
				({ document, change_request, revision }) => {
					sign_in(PRODUCTION);
					open_form(DOCUMENT, document);
					button("New Revision").click();
					form_is(
						REVISION,
						(doc) => doc.__islocal && doc.controlled_document === document
					);
					settle();
					lookup_offers("change_request", change_request).then(
						(listed) =>
							expect(
								listed.filter((entry) => entry.includes(change_request)),
								"the lookup offers the spent request"
							).to.be.empty
					);
					// Typed in full and left, the form does not take it (its lookup does not list it).
					type_link_refused("change_request", change_request);
					// The server's own refusal, as the save would send it.
					refused_call("frappe.client.insert", {
						doc: {
							doctype: REVISION,
							controlled_document: document,
							change_request,
							reviewer: users[QC],
						},
					}).then((message) =>
						expect(message).to.match(
							exact(
								`Change request ${change_request} has already changed Controlled Document ${document} (in Document Revision ${revision}); each change has its own approved change request.`
							)
						)
					);
					no_problems("D9-CC-08, the spent request");

					change_request_form([document, document]);
					send_for_assessment(
						refusal(
							`Controlled Document ${document} is listed twice; list each record once.`
						)
					);
					no_problems("D9-CC-08, a record listed twice");

					sign_in(QA);
					open_form(CHANGE, change_request);
					from_menu("View", "Audit Trail");
					cy.window().should((win) => {
						expect(win.frappe.get_route()[0], "a list is open").to.equal("List");
						expect(win.cur_list?.doctype).to.equal("Audit Event");
					});
					settle();
					cy.call("frappe.client.get_list", {
						doctype: "Audit Event",
						filters: {
							reference_doctype: CHANGE,
							reference_name: change_request,
							action: "Updated after submission",
						},
						fields: ["reason"],
						limit_page_length: 0,
					}).then(({ message }) =>
						expect(message.map((event) => event.reason)).to.include(
							`Controlled Document ${document} changed in Document Revision ${revision}`
						)
					);

					open_form(CHANGE, change_request);
					from_menu("View", "Verify Integrity");
					cy.window()
						.should((win) => {
							const shown = message_dialog(win);
							expect(shown, "the integrity check's answer").to.not.equal(null);
							expect(shown.text).to.match(/Integrity (verified|problem found)/);
							expect(shown.text, "the signatures").to.not.contain(
								"The signatures on this record do not verify."
							);
						})
						.then({ timeout: 10000 }, (win) =>
							close_message(win).then((closed) => expect(closed).to.be.true)
						);
					cy.call("meddevice_suite.compliance.audit_timeline.verify_record", {
						doctype: CHANGE,
						name: change_request,
					}).then(({ message }) => {
						expect(message.signatures_intact, "signatures intact").to.be.true;
						expect(message.audit_trail, "its audit trail").to.be.empty;
					});
					no_problems("D9-CC-08");
				}
			);
		});
	}
);

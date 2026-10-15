// Browser test sheet of data store D9, Quality Records: its risk management (module 39, ISO 14971),
// regulatory licence (module 41) and training (module 37) tests, D9-RISK, D9-LIC and D9-TRN. The
// acceptability policy reviewed by QA and approved by Management; risk assessment revisions
// authored, reviewed and approved by three people under a change request or a risk review; the risk
// management report; risk reviews closed with no change; the plant's licences verified against their
// copy, rejected, renewed and withdrawn, with their obligations and submissions; a change with
// regulatory impact naming its licence; the training matrix changed by cancel and amend; training
// signed Trained, Competent and Not Competent; a competence revoked and the operator refused at the
// job card; and paper training history entered by QA from the Training Record list
// (meddevice_suite/qms/risk_management.py, regulatory_licence.py, training.py; signed acts in
// compliance/signing.py). One test per line of the sheet, in its order, each signed in as the job
// the sheet names and done on the screens as that person would. Refusals are checked word for word;
// a refusal only an API call can reach is posted as that job.
//
// The sheet names pilot users and records; the test site has none, so the run uses the job users
// (meddevice_suite/qa/ui_users.py), the journey's masters (meddevice_suite/qa/ui_journey.py) and
// records of its own (meddevice_suite/qa/sheets/d9b.py):
//   qa.manager, management, production.supervisor, training.coordinator, operator@demo.example.com
//   -> the run's job users; second.qa@demo.example.com -> the journey's second QA person;
//   a pilot risk management file -> a file of a finished good of the test's own, its first revision
//   approved by three people; "the change request of D9-CC-02/06" -> one d9b.py raises and approves;
//   "the work instruction from D9-DOC-03" -> a new one issued through document control;
//   the licence copy of D9-FILE-02 -> uploaded on the screen in D9-LIC-01, made by d9b.py elsewhere;
//   the demo operator -> the journey's operator; a pilot operation -> the journey's first operation.
// Each test starts from records d9b.py makes for it through the code's own services, each step as
// the job that does it, so the tests stand on their own where the sheet builds one on another.

const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const SHEET = "meddevice_suite.qa.sheets.d9b";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const LINE = ".grid-row-open"; // a line of a table, opened in its own form
const TIMEOUT = 20000; // the longest a page or an action may take
const QUIET_MS = 300; // no request in flight for this long: the page has finished
const RELOAD_DELAY_MS = 1000; // the desk reloads a form this long after the server changes it

const QA = "QA Manager";
const SECOND_QA = "Second QA";
const MANAGEMENT = "Management";
const PRODUCTION = "Production Supervisor";
const COORDINATOR = "Training Coordinator";
const OPERATOR = "Operator";

const POLICY = "Risk Acceptability Policy";
const FILE = "Risk Management File";
const ASSESSMENT = "Risk Assessment";
const REPORT = "Risk Management Report";
const REVIEW = "Risk Review";
const LICENCE = "Regulatory Licence";
const RECORD = "Training Record";
const MATRIX = "Training Matrix";

// Plan D-13: the scales a new policy starts with, and the regions of the matrix.
const SEVERITY = ["Negligible", "Minor", "Serious", "Critical", "Catastrophic"];
const PROBABILITY = ["Improbable", "Remote", "Occasional", "Probable", "Frequent"];
const ACCEPTABLE = "Acceptable";
const ALARP = "Review (ALARP)";
const UNACCEPTABLE = "Unacceptable";
const INFORMATION = "Information for safety";
const IMPACT_AREAS = [
	"impact_product_quality",
	"impact_risk",
	"impact_regulatory",
	"impact_validation",
	"impact_training",
	"impact_documents",
	"impact_stock",
];

let password;
let users;
let journey; // the journey's masters (ui_journey.prepare_journey)
let sheet; // what the sheet works with (qa/sheets/d9b.py prepare_sheet)

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

// The Administrator who runs the tests prepares what a test starts from (qa/sheets/d9b.py).
function prepare(method, args = {}) {
	as_administrator();
	return cy.call(`${SHEET}.${method}`, args).then((r) => r.message);
}

// A session kept between tests is checked before it is used: one the server has since ended is
// signed in again (sheet_d4.js).
function signed_in_as(user, pwd) {
	cy.session(
		["sheet-d9b", user],
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

// A server call the signed-in user makes that must be refused; yields the status and the refusal.
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
			return { status: response.status, message: server_message(response.body) };
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
	cy.visit(`/desk/${slug(doctype)}/view/list${query ? `?${query}` : ""}`);
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a list is open").to.equal("List");
		expect(win.cur_list?.doctype, "the list").to.equal(doctype);
	});
	settle();
}

// The names of the records the open list shows.
function listed() {
	settle();
	return cy.window().then((win) => (win.cur_list.data || []).map((row) => row.name));
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

function today(win) {
	return win.frappe.datetime.get_today();
}

function in_days(win, days) {
	return win.frappe.datetime.add_days(today(win), days);
}

function in_months(win, from, months) {
	return win.frappe.datetime.add_months(from, months);
}

// A name of the test's own, so a run never meets an earlier run's record.
function unique() {
	return `${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
}

// Empties the field and types `value`, finding the field again for each step: clearing it can make
// the form redraw it (sheet_d4.js).
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
	// A form redrawn meanwhile can take the typing away with the old input; the person opens the
	// field again as it now stands and types it once more.
	cy.get("body").then(($body) => {
		const $input = $body.find(selector).first();
		const listed_now = $body.find('[role="listbox"]:visible [role="option"]').length > 0;
		if (!listed_now || !$input.length || $input.val() !== value) {
			if (reopen) reopen();
			type_afresh(selector, value);
		}
	});
	// An entry shows the record's title in bold with its name right after it, with no space; the
	// entry named `value` is the one, chosen once the list has stopped changing.
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

// Waits until the open lookup list shows entries and their number has not changed for a while.
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
// list, a choice, a tick, a date or a date and time typed as the person reads it, a text.
function enter(value, kind, selector, reopen = null) {
	cy.window().then((win) => {
		if (kind === "Link") choose(selector, value, reopen);
		else if (kind === "Select") cy.get(first_of(selector)).select(value);
		else if (kind === "Check") cy.get(first_of(selector))[value ? "check" : "uncheck"]();
		else if (kind === "Date" || kind === "Datetime")
			type_and_leave(selector, user_date(win, value));
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

function stored_as(stored, value, label, kind = "Data") {
	if (kind === "Datetime") expect(stored?.slice(0, 16), label).to.equal(value.slice(0, 16));
	else if (typeof value === "number") expect(stored, label).to.be.closeTo(value, 1e-6);
	else expect(stored, label).to.equal(value);
}

// A person types the next field once the form has finished updating from the last one.
function fill(fieldname, value, kind = "Data") {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(fieldname);
	enter(value, kind, inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`));
	cy.window().should((win) => stored_as(current(win)[fieldname], value, fieldname, kind));
}

// Empties a field, as a person deletes what it holds.
function clear_field(fieldname) {
	settle();
	close_dialogs();
	close_open_lists();
	reveal(fieldname);
	const selector = inputs_of(`${FORM} .frappe-control[data-fieldname="${fieldname}"]`);
	cy.get(first_of(selector)).clear();
	cy.get(first_of(selector)).blur();
	settle();
	cy.window().should((win) => expect(current(win)[fieldname], fieldname).to.not.be.ok);
}

function rows(win, table) {
	return current(win)[table] || [];
}

// Makes sure the table has `count` rows, adding them with its Add Row button.
function rows_up_to(table, count) {
	settle();
	reveal(table);
	cy.window().then((win) => {
		const missing = count - rows(win, table).length;
		for (let i = 0; i < missing; i++) {
			cy.get(
				`${FORM} .frappe-control[data-fieldname="${table}"] .grid-add-row:visible:first`
			).click();
		}
	});
	cy.window().should((win) => expect(rows(win, table)).to.have.length(count));
}

function cell(table, idx, fieldname) {
	return `${FORM} .frappe-control[data-fieldname="${table}"] .grid-row[data-idx="${idx}"] [data-fieldname="${fieldname}"]`;
}

function fill_row(table, idx, fieldname, value, kind = "Data") {
	settle();
	close_dialogs();
	close_open_lists();
	cy.get(`${cell(table, idx, fieldname)}:first`).click();
	// Typed once the clicked cell has the cursor (standard_flow.js).
	cy.get(inputs_of(cell(table, idx, fieldname)))
		.first()
		.should("have.focus");
	enter(value, kind, inputs_of(cell(table, idx, fieldname)), () =>
		cy.get(`${cell(table, idx, fieldname)}:first`).click()
	);
	cy.window().should((win) =>
		stored_as(rows(win, table)[idx - 1][fieldname], value, `line ${idx} ${fieldname}`, kind)
	);
}

// A value of a table's row entered as data on the row: for a cell the person cannot reach.
function set_row(table, idx, fieldname, value) {
	settle();
	cy.window().then((win) => {
		const row = rows(win, table)[idx - 1];
		return win.frappe.model.set_value(row.doctype, row.name, fieldname, value);
	});
	settle();
	cy.window().should((win) =>
		expect(rows(win, table)[idx - 1][fieldname], `line ${idx} ${fieldname}`).to.equal(value)
	);
}

// A value of the open line entered as data on the line: for a field the person cannot reach in the
// line's form (a choice the lookup does not offer).
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
	reveal(table);
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
	// A line's own form is longer than the window; the person scrolls to the field first.
	const control = `${LINE} .frappe-control[data-fieldname="${fieldname}"]`;
	cy.get(`${control}:first`).then(($control) => $control[0].scrollIntoView({ block: "center" }));
	enter(value, kind, inputs_of(control));
	cy.window().should((win) =>
		stored_as(win.cur_frm.cur_grid.doc[fieldname], value, fieldname, kind)
	);
}

function close_line() {
	settle();
	close_open_lists();
	// Closed as Frappe closes it: the middle of its heading can land on Delete.
	cy.window().then((win) => win.cur_frm.cur_grid?.toggle_view(false));
	cy.get(LINE).should("not.exist");
	settle();
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

function primary_action() {
	settle(); // the toolbar has finished redrawing
	return cy.get(`${FORM} .page-actions .primary-action:visible`);
}

// Save is pressed once the person has left the field they were in, so nothing typed there reaches
// the form after the save.
function save() {
	leave_field();
	primary_action().should("contain", "Save").click();
	saved();
}

function leave_field() {
	settle();
	close_open_lists();
	cy.document().then((doc) => doc.activeElement?.blur?.());
	settle();
}

function saved() {
	cy.window().should((win) => {
		const frm = win.cur_frm;
		const which = `${frm.doctype} ${frm.docname}`;
		expect(frm.is_new(), `${which} saved`).to.not.be.ok;
		expect(frm.is_dirty(), `${which}: nothing left unsaved`).to.not.be.ok;
	});
	settle();
}

// Save is pressed and the server refuses it with the sheet's message; the form stays as it was.
function save_refused({ server, screen }) {
	expect_refusal(server);
	primary_action().should("contain", "Save").click();
	refused_with(screen);
	settle();
	close_dialogs();
}

// The current dialog has finished fading in (sheet_d4.js).
function opened_fully() {
	cy.window().should((win) => {
		const $wrapper = win.cur_dialog?.$wrapper;
		expect($wrapper?.is(".modal.show"), "a dialog is open").to.equal(true);
		expect($wrapper.css("opacity"), "it has finished opening").to.equal("1");
	});
}

// ERPNext's "Getting Started" panel floats over the right of the form; the person closes it.
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

// Closes whatever dialog is left over the form, the topmost first, until none is left. A refusal
// left on screen is a problem of the step and is reported (sheet_d4.js).
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

// A field of the open dialog filled as the person fills it, then checked in the dialog.
function dialog_fill(fieldname, value, kind = "Data") {
	settle();
	const selector = inputs_of(`.modal.show .frappe-control[data-fieldname="${fieldname}"]`);
	if (kind === "Date") {
		// Picked in the dialog's date field, as lot_dates.js does: its picker covers the dialog.
		cy.window()
			.its("cur_dialog")
			.then((dialog) => dialog.set_value(fieldname, value));
	} else {
		enter(value, kind, selector);
	}
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value(fieldname), fieldname).to.equal(value));
}

function press_dialog(label) {
	settle();
	cy.get(".modal.show .btn-primary:visible").contains(label).click();
}

// A change to a regulated record asks its reason first (audit_reason.js); given when asked. Done
// once the next dialog the action opens is shown (`next`), or the action has finished (`done`).
function give_reason_if_asked(reason, done) {
	cy.window().then({ timeout: TIMEOUT + 5000 }, (win) =>
		until(
			() =>
				(win.cur_dialog?.display && /^Reason for /.test(win.cur_dialog.title)) ||
				win.frappe.msg_dialog?.$wrapper.is(":visible") ||
				done(win)
		).then((shown) => {
			expect(shown, "a reason is asked or the action goes on").to.be.true;
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

// The button `label` opens the signing dialog of the act `act`.
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

// Signs `label` with the reason, when the act asks one, and the run's password; the form reloads
// the signed record.
function sign(label, reason = null, act = label) {
	open_signing(label, act);
	if (reason) enter_reason(reason);
	else cy.get('.modal.show textarea[data-fieldname="reason"]').should("not.exist");
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

// The record's signatures include the act with its meaning, shown on the form.
function signed_as(action, meaning, reason = null) {
	cy.window().should((win) => {
		const signed = (current(win).__onload?.signatures || []).map((s) => [s.action, s.meaning]);
		expect(signed, "electronic signatures").to.deep.include([action, meaning]);
		if (reason) {
			const row = current(win).__onload.signatures.find((s) => s.action === action);
			expect(row.reason, `the reason signed with ${action}`).to.equal(reason);
		}
	});
	cy.contains(FORM, "Electronic signatures").should("be.visible");
}

function status_is(status, docstatus = null) {
	cy.window().should((win) => {
		expect(current(win).status, "status").to.equal(status);
		if (docstatus !== null) expect(current(win).docstatus, "docstatus").to.equal(docstatus);
	});
}

// --- Reading what is stored, as the signed-in person ----------------------------------------

function stored(doctype, name) {
	return cy.call("frappe.client.get", { doctype, name }).then((r) => r.message);
}

function records(doctype, filters, fields = ["name"]) {
	return cy
		.call("frappe.client.get_list", { doctype, filters, fields, limit_page_length: 0 })
		.then((r) => r.message);
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

// The attachment of an Attach field, uploaded privately as the attach control does.
function attach(fieldname, name) {
	upload_private_file(name).then((url) =>
		cy.window().then((win) => win.cur_frm.set_value(fieldname, url))
	);
	settle();
	cy.window().should((win) =>
		expect(current(win)[fieldname], fieldname).to.match(/^\/private\/files\//)
	);
}

// --- Steps the jobs take again and again ----------------------------------------------------

// The demo matrix of plan D-13: severity counts double, so a swapped axis shows; the policy in
// force on the test site is set up this way (setup/fixtures/risk.py), and the new one keeps it.
function acceptability(severity, probability) {
	const weight = 2 * severity + probability;
	if (weight >= 12) return UNACCEPTABLE;
	return weight >= 8 ? ALARP : ACCEPTABLE;
}

// The acceptability of a row of the policy's matrix. Its choice never takes the cursor when its cell
// is clicked (found in Chrome, 2026-10-15), so it is entered as data on the row, as for any field
// the person cannot reach.
function decide(idx, value) {
	set_row("matrix", idx, "acceptability", value);
}

// Every row of the policy's matrix given its acceptability, in the matrix's order.
function decide_matrix(decision) {
	cy.window().then((win) =>
		rows(win, "matrix").forEach((row) =>
			decide(row.idx, decision(row.severity, row.probability))
		)
	);
}

// The pairs of a 5 x 5 matrix not in `decided`, as the policy's refusal names them.
function undecided(decided) {
	const pairs = [];
	for (let s = 1; s <= 5; s++) {
		for (let p = 1; p <= 5; p++) {
			if (!decided.some(([ds, dp]) => ds === s && dp === p))
				pairs.push(`severity ${s}, probability ${p}`);
		}
	}
	return pairs.join(", ");
}

// A hazard of the open assessment, entered in its line's own form.
function enter_hazard(idx, hazard) {
	rows_up_to("hazards", idx);
	open_line("hazards", idx);
	fill_line("hazard_id", hazard.id);
	fill_line("hazard_category", "Energy", "Select");
	fill_line("hazard", hazard.hazard);
	fill_line("sequence_of_events", "Balloon over-inflated by the user (browser test)");
	fill_line("hazardous_situation", "Balloon wall gives way in the bladder");
	fill_line("harm", "Fragments left in the bladder; surgery to retrieve them");
	fill_line("severity", hazard.severity);
	fill_line("probability", hazard.probability);
	fill_line("residual_severity", hazard.severity);
	fill_line("residual_probability", hazard.probability);
	fill_line("justification", hazard.justification);
	close_line();
}

// The Training Record list's Add button: "Add Training Record", shortened to "Add" where the
// page head is too narrow for it (list_view.js).
function add_record_button() {
	return primary_action().should(($button) =>
		expect($button.text().trim(), "the list's Add button").to.match(/^Add( Training Record)?$/)
	);
}

// QA starts paper history from the Training Record list's Add button.
function start_history(employee, document) {
	open_list(RECORD);
	add_record_button().click();
	open_dialog().should("contain", "Record Training History");
	dialog_fill("employee", employee, "Link");
	dialog_fill("controlled_document", document, "Link");
	press_dialog("Start");
}

// Starting the history is refused with `expected`; the dialog is closed after.
function start_history_refused(employee, document, expected) {
	expect_refusal(expected.server);
	start_history(employee, document);
	refused_with(expected.screen);
	close_dialogs();
}

function open_records_of(employee, revision, trigger) {
	return records(
		RECORD,
		{ employee, document_revision: revision, status: "Open", docstatus: 0, trigger },
		["name", "trigger", "due_date", "status"]
	);
}

// --- The sheet ------------------------------------------------------------------------------

context("D9 Quality Records: risk, licences and training, as each job", () => {
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
			sheet = message;
			users[SECOND_QA] = sheet.second_qa;
		});
	});

	// D9-TRN-04 revokes the operator's competence on a journey operation; the operator is trained
	// again whatever happened in it, for the specs that log work on the journey's operations.
	after(() => {
		if (password) prepare("restore_operator_training", { password });
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

	// --- Risk management (module 39) ----------------------------------------------------------

	it("[URS-QMS-017] D9-RISK-01 QA reviews a new acceptability policy, Management returns it, then approves it", () => {
		const made = {};
		sign_in(QA);
		cy.call("frappe.client.get_value", {
			doctype: POLICY,
			filters: { status: "In Force", docstatus: 1 },
			fieldname: "name",
		}).then(({ message }) => {
			made.previous = message?.name;
			expect(made.previous, "a policy in force before").to.be.ok;
		});

		new_form(POLICY);
		fill("title", `Risk acceptability policy (browser test D9 ${unique()})`);
		// The new policy starts with the D-13 scales and every pair of the matrix listed.
		save();
		cy.window().should((win) => {
			const doc = current(win);
			made.policy = doc.name;
			expect(doc.status).to.equal("Draft");
			expect(doc.severity_levels.map((row) => [row.level, row.level_name])).to.deep.equal(
				SEVERITY.map((name, i) => [i + 1, name])
			);
			expect(doc.probability_levels.map((row) => [row.level, row.level_name])).to.deep.equal(
				PROBABILITY.map((name, i) => [i + 1, name])
			);
			expect(doc.matrix, "the matrix's rows").to.have.length(25);
		});

		fill(
			"policy_statement",
			"ISO 14971 Annex C, the state of the art for urinary catheters and the plant's complaint history (browser test)."
		);
		decide_matrix(acceptability);
		save();
		sign("Sign as Reviewed", null, "Review");
		cy.window().should((win) => {
			expect(current(win).status).to.equal("Reviewed");
			expect(current(win).reviewed_by).to.equal(users[QA]);
		});
		signed_as("Review", "Reviewed");
		no_problems("D9-RISK-01, QA reviews the policy");

		sign_in(MANAGEMENT);
		cy.then(() => open_form(POLICY, made.policy));
		offered("Approve", true);
		sign(
			"Return for Changes",
			"Say which complaints the probability scale was set from",
			"Return"
		);
		cy.window().should((win) => {
			expect(current(win).status).to.equal("Draft");
			expect(current(win).reviewed_by, "Reviewed By cleared").to.not.be.ok;
		});
		signed_as("Return", "Rejected", "Say which complaints the probability scale was set from");
		no_problems("D9-RISK-01, Management returns the policy");

		sign_in(QA);
		cy.then(() => open_form(POLICY, made.policy));
		sign("Sign as Reviewed", null, "Review");
		status_is("Reviewed");
		no_problems("D9-RISK-01, QA reviews it again");

		sign_in(MANAGEMENT);
		cy.then(() => open_form(POLICY, made.policy));
		sign("Approve", null, "Approve");
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.docstatus, "approved").to.equal(1);
			expect(doc.status).to.equal("In Force");
			expect(doc.approved_by).to.equal(users[MANAGEMENT]);
			expect(doc.in_force_since).to.equal(today(win));
		});
		cy.window().then((win) =>
			banner("green", `In force since ${user_date(win, today(win))}.`)
		);
		signed_as("Approve", "Approved");
		cy.then(() => stored(POLICY, made.previous)).then((previous) => {
			expect(previous.status, "the policy in force before").to.equal("Superseded");
			expect(previous.superseded_by).to.equal(made.policy);
		});
		no_problems("D9-RISK-01");
	});

	it("[URS-QMS-017] D9-RISK-02 An incomplete or inconsistent policy cannot be reviewed", () => {
		// The draft QA started (d9b.py): a new policy saved from the form is the subject of D9-RISK-01.
		prepare("prepare_draft_policy", { password }).then((policy) => {
			sign_in(QA);
			open_form(POLICY, policy.name);
			cy.window().should((win) => {
				expect(current(win).status).to.equal("Draft");
				expect(rows(win, "matrix")).to.have.length(25);
			});
			fill(
				"policy_statement",
				"ISO 14971 Annex C and the state of the art (browser test D9)."
			);
			decide(1, ACCEPTABLE);
			decide(2, ACCEPTABLE);
			save();
			sign_refused(
				"Sign as Reviewed",
				null,
				refusal(
					`Decide the acceptability of every pair: ${undecided([
						[1, 1],
						[1, 2],
					])}.`
				),
				"Review"
			);

			decide_matrix(() => ALARP);
			cy.window().should((win) =>
				expect([
					rows(win, "matrix")[5].severity,
					rows(win, "matrix")[5].probability,
				]).to.deep.equal([2, 1])
			);
			decide(6, ACCEPTABLE);
			save();
			sign_refused(
				"Sign as Reviewed",
				null,
				refusal(
					"Severity 2, probability 1 cannot be more acceptable than severity 1, probability 1."
				),
				"Review"
			);
			cy.then(() => stored(POLICY, policy.name)).then((doc) => {
				expect(doc.status, "the policy stays a draft").to.equal("Draft");
				expect(doc.reviewed_by).to.not.be.ok;
			});
			no_problems("D9-RISK-02");
		});
	});

	it("[URS-QMS-019] [URS-QMS-020] [URS-QMS-021] [URS-QMS-022] [URS-QMS-024] D9-RISK-03 A risk assessment revision under a risk review is authored, reviewed and approved by three people", () => {
		prepare("prepare_risk_file", { password, stage: "change_approved" }).then((risk) => {
			const revision = `${risk.file}-R02`;
			sign_in(QA);
			open_form(REVIEW, risk.review);
			cy.window().should((win) => {
				expect(current(win).status).to.equal("Open");
				expect(current(win).trigger).to.equal("Change Request");
				expect(current(win).source_name).to.equal(risk.change_request);
			});
			button("Start New Revision").click();
			form_is(ASSESSMENT, (doc) => doc.name === revision);
			settle();
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.status).to.equal("Draft");
				expect(doc.risk_review).to.equal(risk.review);
				expect(doc.author).to.equal(users[QA]);
				// The hazards and controls of the revision in force, with residual ratings and evidence.
				expect(rows(win, "hazards").map((row) => row.hazard_id)).to.deep.equal(["H-01"]);
				for (const row of rows(win, "hazards")) {
					expect(row.residual_severity, `${row.hazard_id} residual severity`).to.be.ok;
					expect(row.residual_probability, `${row.hazard_id} residual probability`).to.be
						.ok;
					// Worked out from the policy in force, not typed.
					expect(row.initial_acceptability).to.equal(
						acceptability(row.severity, row.probability)
					);
					expect(row.residual_acceptability).to.equal(
						acceptability(row.residual_severity, row.residual_probability)
					);
				}
				expect(rows(win, "controls")).to.have.length(1);
				for (const row of rows(win, "controls")) {
					expect(row.implementation_evidence, `control ${row.idx} implementation`).to.be
						.ok;
					expect(row.effectiveness_evidence, `control ${row.idx} effectiveness`).to.be
						.ok;
					expect(row.pinned_version, "no version pinned on a draft").to.not.be.ok;
				}
				for (const field of ["initial_acceptability", "residual_acceptability"]) {
					expect(
						win.frappe.meta.get_docfield("Risk Hazard", field).read_only,
						`${field} is not typed`
					).to.be.ok;
				}
			});
			fill("reviewer", users[PRODUCTION], "Link");
			fill(
				"summary_of_change",
				"Balloon wall thickness raised under the change request (browser test)."
			);
			save();
			sign("Sign as Author", null, "Author");
			status_is("In Review");
			signed_as("Author", "Authored");
			no_problems("D9-RISK-03, QA authors the revision");

			sign_in(PRODUCTION);
			open_form(ASSESSMENT, revision);
			sign("Sign as Reviewer", null, "Review");
			status_is("Awaiting Approval");
			signed_as("Review", "Reviewed");
			no_problems("D9-RISK-03, the Production Supervisor reviews it");

			sign_in(SECOND_QA);
			open_form(ASSESSMENT, revision);
			sign("Approve", null, "Approve");
			status_is("Approved", 1);
			cy.window().should((win) => {
				expect(current(win).approver).to.equal(sheet.second_qa);
				for (const row of rows(win, "controls")) {
					expect(row.pinned_version, `control ${row.idx}'s pinned version`).to.be.ok;
				}
			});
			signed_as("Approve", "Approved");
			stored(ASSESSMENT, risk.revision).then((before) => {
				expect(before.status, "the previous revision").to.equal("Superseded");
				expect(before.superseded_by).to.equal(revision);
			});
			stored(REPORT, risk.report).then((report) =>
				expect(report.status, "the previous report").to.equal("Superseded")
			);
			stored(FILE, risk.file).then((file) => {
				expect(file.current_revision, "the revision in force").to.equal(revision);
				expect(file.current_report, "no report in force yet").to.not.be.ok;
			});
			stored(REVIEW, risk.review).then((review) => {
				expect(review.status).to.equal("Closed");
				expect(review.outcome).to.equal("New revision");
				expect(review.resulting_revision).to.equal(revision);
			});
			no_problems("D9-RISK-03");
		});
	});

	it("[URS-QMS-020] [URS-QMS-022] [URS-QMS-024] D9-RISK-04 An unacceptable hazard needs a proper control; the reviewer returns the revision", () => {
		prepare("prepare_risk_file", { password, stage: "change_approved" }).then((risk) => {
			const revision = `${risk.file}-R02`;
			sign_in(QA);
			open_form(FILE, risk.file);
			button("Start New Revision").click();
			open_dialog().should("contain", "Name the approved change request");
			const unnamed = refusal(
				`${risk.file} has an approved risk assessment: name the approved change request or the open risk review for this revision.`
			);
			expect_refusal(unnamed.server);
			press_dialog("Start");
			refused_with(unnamed.screen);
			close_dialogs();

			button("Start New Revision").click();
			open_dialog().should("contain", "Name the approved change request");
			dialog_fill("change_request", risk.change_request, "Link");
			press_dialog("Start");
			form_is(ASSESSMENT, (doc) => doc.name === revision);
			settle();
			cy.window().should((win) => {
				expect(current(win).change_request).to.equal(risk.change_request);
				expect(current(win).risk_review, "the review the change raised").to.equal(
					risk.review
				);
			});
			fill("reviewer", users[PRODUCTION], "Link");
			fill("summary_of_change", "Over-inflation of the balloon analysed (browser test).");
			enter_hazard(2, {
				id: "H-99",
				hazard: "Balloon over-inflated",
				severity: 5,
				probability: 5,
				justification:
					"No further reduction is practicable before the control (browser test).",
			});
			save();
			cy.window().should((win) =>
				expect(
					rows(win, "hazards")[1].initial_acceptability,
					"H-99 before control"
				).to.equal(UNACCEPTABLE)
			);
			sign_refused(
				"Sign as Author",
				null,
				refusal("Hazard H-99 is unacceptable: add a risk control measure."),
				"Author"
			);

			// A control of information for safety implemented by an SOP. The lookup offers only
			// Packaging and Labelling documents for it; the SOP is entered as the line's data.
			rows_up_to("controls", 2);
			open_line("controls", 2);
			fill_line("hazard_id", "H-99");
			fill_line("option", INFORMATION, "Select");
			fill_line(
				"description",
				"Warning in the instructions for use: inflate with 10 ml only"
			);
			fill_line("control_doctype", "Controlled Document", "Select");
			set_line("control_name", risk.sop);
			close_line();
			save_refused(
				refusal(`Control 2: ${INFORMATION} is a Packaging and Labelling document.`)
			);

			open_line("controls", 2);
			fill_line("control_name", risk.labelling, "Link");
			fill_line("implementation_evidence_type", "Controlled Document", "Select");
			fill_line("implementation_evidence", risk.labelling, "Link");
			fill_line("effectiveness_evidence_type", "Controlled Document", "Select");
			fill_line("effectiveness_evidence", risk.labelling, "Link");
			close_line();
			save();
			sign("Sign as Author", null, "Author");
			status_is("In Review");
			no_problems("D9-RISK-04, QA authors the revision");

			sign_in(PRODUCTION);
			open_form(ASSESSMENT, revision);
			sign(
				"Return for Changes",
				"Say how the warning's effectiveness was checked",
				"Return"
			);
			status_is("Draft", 0);
			signed_as("Return", "Rejected", "Say how the warning's effectiveness was checked");
			no_problems("D9-RISK-04");
		});
	});

	it("[URS-QMS-022] [URS-QMS-023] D9-RISK-05 QA withdraws an unfinished assessment revision and an unfinished report", () => {
		prepare("prepare_risk_file", { password, stage: "revision_in_progress" }).then((risk) => {
			const reason = "Analysis found nothing to change";
			sign_in(QA);
			open_form(FILE, risk.file);
			banner("blue", `Revision ${risk.draft} is in progress.`);
			offered("Start New Revision", false);

			open_form(ASSESSMENT, risk.draft);
			sign("Withdraw", reason);
			status_is("Withdrawn", 0);
			signed_as("Withdraw", "Rejected", reason);
			open_form(FILE, risk.file);
			offered("Start New Revision", true);
			no_problems("D9-RISK-05, QA withdraws the revision");

			const made = {};
			new_form(REPORT);
			fill("risk_assessment", risk.revision, "Link");
			save();
			cy.window().then((win) => {
				made.report = current(win).name;
				expect(current(win).status).to.equal("Draft");
			});
			sign("Withdraw", "Started on the wrong file (browser test)");
			status_is("Withdrawn", 0);
			signed_as("Withdraw", "Rejected", "Started on the wrong file (browser test)");

			// A new report can be started on the same assessment.
			new_form(REPORT);
			fill("risk_assessment", risk.revision, "Link");
			save();
			cy.window().should((win) => {
				expect(current(win).name).to.not.equal(made.report);
				expect(current(win).status).to.equal("Draft");
			});
			no_problems("D9-RISK-05");
		});
	});

	it("[URS-QMS-023] D9-RISK-06 QA authors the risk management report; a second QA returns it, then approves it", () => {
		prepare("prepare_risk_file", { password, stage: "in_force" }).then((risk) => {
			const made = {};
			sign_in(QA);
			new_form(REPORT);
			fill("risk_assessment", risk.revision, "Link");
			fill(
				"plan_review",
				"Each activity of the plan checked against its record (browser test D9)."
			);
			fill(
				"overall_conclusion",
				"The residual risk is as low as reasonably practicable and outweighed by the benefits of drainage (browser test D9)."
			);
			rows_up_to("information_sources", 1);
			fill_row("information_sources", 1, "controlled_document", risk.sop, "Link");
			save();
			cy.window().should((win) => {
				const doc = current(win);
				made.report = doc.name;
				expect(doc.status).to.equal("Draft");
				expect(doc.author).to.equal(users[QA]);
				expect(doc.risk_management_file).to.equal(risk.file);
				expect(doc.plan_implemented, "left unticked").to.equal(0);
			});
			sign_refused(
				"Sign as Author",
				null,
				refusal(
					"Confirm the risk management plan was implemented, and say how it was checked."
				),
				"Author"
			);
			fill("plan_implemented", 1, "Check");
			save();
			sign("Sign as Author", null, "Author");
			status_is("Awaiting Approval");
			no_problems("D9-RISK-06, QA authors the report");

			sign_in(SECOND_QA);
			cy.then(() => open_form(REPORT, made.report));
			sign("Return for Changes", "Name the complaint trend review as well", "Return");
			status_is("Draft", 0);
			signed_as("Return", "Rejected");
			no_problems("D9-RISK-06, the second QA returns it");

			sign_in(QA);
			cy.then(() => open_form(REPORT, made.report));
			sign("Sign as Author", null, "Author");
			status_is("Awaiting Approval");
			no_problems("D9-RISK-06, QA signs it again");

			sign_in(SECOND_QA);
			cy.then(() => open_form(REPORT, made.report));
			sign("Approve", null, "Approve");
			status_is("Approved", 1);
			cy.window().should((win) => expect(current(win).approver).to.equal(sheet.second_qa));
			signed_as("Approve", "Approved");
			cy.then(() => stored(FILE, risk.file)).then((file) =>
				expect(file.current_report, "the file's report in force").to.equal(made.report)
			);
			no_problems("D9-RISK-06");
		});
	});

	it("[URS-QMS-024] [URS-QMS-025] D9-RISK-07 QA closes a risk review with no change", () => {
		prepare("prepare_risk_file", { password, stage: "change_approved" }).then((risk) => {
			prepare("prepare_risk_file", { password, stage: "revision_in_progress" }).then(
				(busy) => {
					const reason = "The change does not affect any hazard or control";
					sign_in(QA);
					open_form(REVIEW, risk.review);
					sign("Close: No Change", reason);
					cy.window().should((win) => {
						const doc = current(win);
						expect(doc.docstatus, "closed").to.equal(1);
						expect(doc.status).to.equal("Closed");
						expect(doc.outcome).to.equal("No change");
						expect(doc.rationale).to.equal(reason);
						expect(doc.closed_by).to.equal(users[QA]);
						expect(doc.closed_on?.slice(0, 10)).to.equal(today(win));
					});
					signed_as("Close: No Change", "Reviewed", reason);

					// A review with a revision in progress under it is not closed.
					open_form(REVIEW, busy.review);
					sign_refused(
						"Close: No Change",
						reason,
						refusal(
							`Revision ${busy.draft} is in progress for this review; approve it, or withdraw it before closing the review.`
						)
					);
					stored(REVIEW, busy.review).then((review) =>
						expect(review.status, "the review stays open").to.equal("Open")
					);
					no_problems("D9-RISK-07");
				}
			);
		});
	});

	// --- Regulatory licences (module 41) ------------------------------------------------------

	it("[URS-REG-001] [URS-REG-003] D9-LIC-01 QA records the plant licence with its copy and verifies it", () => {
		const made = { tag: unique() };
		const reason = "Checked against the authority's original";
		sign_in(QA);
		new_form("Regulatory Licence Type");
		fill("licence_type_name", `Manufacturing licence (test) ${made.tag}`);
		fill("has_expiry", 1, "Check");
		save();
		cy.window().then((win) => {
			made.type = current(win).name;
		});

		// The copy, uploaded into the important files repository (D9-FILE-02).
		new_form("Important File");
		fill("title", `Manufacturing licence copy ${made.tag}`);
		fill("category", sheet.file_category, "Link");
		attach("new_file", `licence-copy-${made.tag}.pdf`);
		save();
		cy.window().should((win) => {
			made.copy = current(win).name;
			expect(current(win).current_version, "the copy's first version").to.equal(1);
		});

		new_form(LICENCE);
		cy.then(() => fill("licence_type", made.type, "Link"));
		fill("licence_number", `MFG/MD-5/${made.tag}`);
		fill("issuing_authority", "State Licensing Authority");
		cy.window().then((win) => {
			fill("valid_from", in_days(win, -30), "Date");
			fill("valid_until", in_days(win, 730), "Date");
		});
		cy.then(() => fill("important_file", made.copy, "Link"));
		rows_up_to("products", 1);
		fill_row("products", 1, "item_code", sheet.product, "Link");
		rows_up_to("obligations", 1);
		fill_row("obligations", 1, "obligation", "Retention fee");
		cy.window().then((win) => {
			made.fee_due = in_days(win, 60);
			fill_row("obligations", 1, "due_date", made.fee_due, "Date");
		});
		save();
		cy.window().then((win) => {
			made.licence = current(win).name;
			expect(current(win).status).to.equal("Draft");
		});
		sign("Verify", reason);
		status_is("Verified", 1);
		signed_as("Verify", "Verified", reason);
		cy.then(() => stored("Important File", made.copy)).then((copy) => {
			const version = copy.versions.find((row) => row.version === copy.current_version);
			cy.window().should((win) => {
				expect(current(win).verified_file_version, "Version Verified").to.equal(
					version.version
				);
				expect(current(win).verified_file_sha256, "Fingerprint Verified").to.equal(
					version.file_sha256
				);
			});
		});
		no_problems("D9-LIC-01, QA verifies the licence");

		// A new version of the copy, uploaded after the verification.
		cy.then(() => open_form("Important File", made.copy));
		attach("new_file", `licence-copy-${made.tag}-v2.pdf`);
		fill("new_note", "Copy re-scanned (browser test)");
		save();
		cy.window().should((win) => expect(current(win).current_version).to.equal(2));
		cy.then(() => open_form(LICENCE, made.licence));
		banner("orange", "The copy has a newer version (2) than the one QA verified (1).");
		no_problems("D9-LIC-01, a new copy");

		// The daily run: QA is reminded of the retention fee, due within the lead time.
		cy.then(() =>
			prepare("run_daily_reminders", {
				category: "Regulatory Licence",
				reference_doctype: LICENCE,
				reference_name: made.licence,
			})
		).then((reminders) => {
			expect(sheet.licence_warning_days, "QA's lead time").to.be.at.least(60);
			expect(
				reminders.filter((row) => row.status === "Open").map((row) => row.subject),
				"raised by the daily run"
			).to.have.length.at.least(1);
		});
		sign_in(QA);
		cy.then(() => open_list("Reminder", { reference_name: made.licence }));
		cy.then(() =>
			records(
				"Reminder",
				{ reference_doctype: LICENCE, reference_name: made.licence, status: "Open" },
				["name", "subject", "due_date", "category"]
			)
		).then((open) => {
			cy.window().then((win) => {
				const fee = open.find((row) => row.subject.startsWith("Retention fee"));
				expect(fee, "QA's reminder of the retention fee").to.be.ok;
				expect(fee.category).to.equal("Regulatory Licence");
				expect(fee.due_date).to.equal(made.fee_due);
				expect(fee.subject).to.equal(
					`Retention fee of licence MFG/MD-5/${made.tag} is due on ${user_date(
						win,
						made.fee_due
					)}`
				);
			});
			listed().then((names) =>
				expect(names, "the reminder in QA's list").to.include.members(
					open.map((row) => row.name)
				)
			);
		});
		no_problems("D9-LIC-01");
	});

	it("[URS-REG-001] D9-LIC-02 A licence is not verified when expired, without a copy, or with wrong dates", () => {
		prepare("prepare_licence", { password, stage: "copy" }).then((lic) => {
			const reason = "Checked against the authority's original";
			sign_in(QA);
			new_form(LICENCE);
			fill("licence_type", sheet.licence_type, "Link");
			fill("licence_number", `MFG/MD-5/${unique()}`);
			fill("issuing_authority", "State Licensing Authority");
			cy.window().then((win) => {
				fill("valid_from", in_days(win, -10), "Date");
				fill("valid_until", in_days(win, -20), "Date");
			});
			save_refused(
				refusal("The licence is valid from a day up to a later one; check its dates.")
			);
			cy.window().should((win) => expect(win.cur_frm.is_new(), "not saved").to.be.ok);

			cy.window().then((win) => {
				fill("valid_from", in_days(win, -400), "Date");
				fill("valid_until", in_days(win, -1), "Date");
			});
			fill("important_file", lic.copy, "Link");
			save();
			cy.window().then((win) =>
				sign_refused(
					"Verify",
					reason,
					refusal(
						`The licence expired on ${user_date(
							win,
							in_days(win, -1)
						)}; it cannot be verified.`
					)
				)
			);

			cy.window().then((win) => fill("valid_until", in_days(win, 365), "Date"));
			clear_field("important_file");
			save();
			sign_refused(
				"Verify",
				reason,
				refusal("Attach the licence's copy as an important file.")
			);
			cy.window()
				.then((win) => stored(LICENCE, current(win).name))
				.then((licence) => {
					expect(licence.docstatus, "still a draft").to.equal(0);
					expect(licence.status).to.equal("Draft");
				});
			no_problems("D9-LIC-02");
		});
	});

	it("[URS-REG-002] D9-LIC-03 QA rejects a draft, and a verified renewal supersedes the old licence", () => {
		prepare("prepare_licence", { password, stage: "verified" }).then((lic) => {
			const made = {};
			const renewal = (until_days) => {
				new_form(LICENCE);
				fill("licence_type", sheet.licence_type, "Link");
				fill("licence_number", `${lic.number}/R`);
				fill("issuing_authority", "State Licensing Authority");
				fill("renews", lic.licence, "Link");
				cy.window().then((win) => {
					fill("valid_from", today(win), "Date");
					fill("valid_until", in_days(win, until_days), "Date");
				});
				fill("important_file", lic.copy, "Link");
				save();
			};
			sign_in(QA);
			renewal(1095);
			cy.window().then((win) => {
				made.rejected = current(win).name;
			});
			sign("Reject", "Number does not match the copy");
			status_is("Rejected", 1);
			signed_as("Reject", "Rejected", "Number does not match the copy");
			stored(LICENCE, lic.licence).then((old) =>
				expect(old.status, "the old licence").to.equal("Verified")
			);
			no_problems("D9-LIC-03, QA rejects the first renewal");

			renewal(1460);
			cy.window().then((win) => {
				made.renewal = current(win).name;
				expect(current(win).valid_until > lic.valid_until, "a later Valid Until").to.be
					.true;
			});
			sign("Verify", "Renewal checked against the authority's original");
			status_is("Verified", 1);
			open_form(LICENCE, lic.licence);
			cy.window().should((win) => {
				expect(current(win).status, "the old licence").to.equal("Superseded");
				expect(current(win).superseded_by).to.equal(made.renewal);
			});
			no_problems("D9-LIC-03");
		});
	});

	it("[URS-REG-002] [URS-REG-004] [URS-REG-005] D9-LIC-04 Obligations and submissions on a verified licence; QA withdraws it", () => {
		prepare("prepare_licence", { password, stage: "verified" }).then((lic) => {
			const made = {};
			sign_in(QA);
			open_form(LICENCE, lic.licence);
			button("Add Obligation").click();
			open_dialog().should("contain", "Add an obligation of the licence");
			dialog_fill("obligation", "Surveillance audit");
			cy.window().then((win) => {
				made.audit_due = in_months(win, today(win), 6);
				dialog_fill("due_date", made.audit_due, "Date");
			});
			press_dialog("Submit");
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			cy.window().should((win) => {
				const row = rows(win, "obligations").find(
					(line) => line.obligation === "Surveillance audit"
				);
				expect(row, "the new obligation").to.be.ok;
				expect(row.due_date).to.equal(made.audit_due);
				expect(row.added_after_verification, "Added After Verification").to.equal(1);
				made.row = row.name;
			});

			button("Obligation Done").click();
			open_dialog().should("contain", "Record an obligation as done");
			cy.then(() => dialog_fill("row", made.row, "Select"));
			dialog_fill("evidence", lic.evidence, "Link");
			press_dialog("Submit");
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			cy.window().should((win) => {
				const row = rows(win, "obligations").find((line) => line.name === made.row);
				expect(row.done_on, "Done On").to.equal(today(win));
				expect(row.done_by, "Recorded By").to.equal(users[QA]);
				expect(row.evidence).to.equal(lic.evidence);
			});

			button("Record Submission").click();
			open_dialog().should("contain", "Record a submission to the authority");
			dialog_fill("submission_type", "Change Notification", "Select");
			cy.window().then((win) => dialog_fill("submitted_on", today(win), "Date"));
			dialog_fill("authority_reference", `CDSCO/CN/${unique()}`);
			dialog_fill("important_file", lic.submission_copy, "Link");
			press_dialog("Submit");
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			cy.window().should((win) => {
				const [row] = rows(win, "submissions");
				expect(row, "the submission").to.be.ok;
				expect(row.submission_type).to.equal("Change Notification");
				expect(row.submitted_on).to.equal(today(win));
				expect(row.important_file).to.equal(lic.submission_copy);
				expect(row.recorded_by, "Recorded By").to.equal(users[QA]);
			});
			no_problems("D9-LIC-04, obligations and a submission");

			sign("Withdraw", "Suspended by the authority (test)");
			status_is("Withdrawn", 1);
			cy.window().should((win) =>
				expect(current(win).reason).to.equal("Suspended by the authority (test)")
			);
			signed_as("Withdraw", "Rejected", "Suspended by the authority (test)");
			for (const label of [
				"Add Obligation",
				"Obligation Done",
				"Record Submission",
				"Withdraw",
			]) {
				offered(label, false);
			}
			no_problems("D9-LIC-04");
		});
	});

	it("[URS-REG-006] D9-LIC-05 A change with regulatory impact must list a verified licence", () => {
		prepare("prepare_regulatory_change", { password }).then((change) => {
			sign_in(QA);
			open_form("Change Request", change.change_request);
			status_is("Under Assessment");
			for (const field of IMPACT_AREAS) {
				fill(field, field === "impact_regulatory" ? "Yes" : "No", "Select");
			}
			fill("classification", "Major", "Select");
			fill(
				"impact_summary",
				"A new sterilisation site is notified to the licensing authority (browser test)."
			);
			fill("effectiveness_check_days", 90);
			save();
			sign_refused(
				"Assess",
				null,
				refusal(
					"A change with regulatory impact lists the verified licence or registration it affects."
				)
			);
			status_is("Under Assessment");

			rows_up_to("affected_records", 2);
			fill_row("affected_records", 2, "reference_doctype", LICENCE, "Select");
			fill_row("affected_records", 2, "reference_name", change.licence, "Link");
			save();
			sign("Assess");
			status_is("Awaiting Approval");
			signed_as("Assess", "Reviewed");
			no_problems("D9-LIC-05");
		});
	});

	// --- Training (module 37) -----------------------------------------------------------------

	it("[URS-TRN-004] [URS-TRN-001] D9-TRN-01 QA changes the Machine Operator matrix by cancel and amend and approves it", () => {
		prepare("prepare_training", { password, stage: "new_instruction" }).then((t) => {
			const made = {};
			const reason = "Work instruction added to the job";
			sign_in(QA);
			new_form(MATRIX);
			fill("designation", sheet.operator_job, "Link");
			rows_up_to("items", 1);
			fill_row("items", 1, "controlled_document", t.instruction, "Link");
			save();
			sign_refused(
				"Approve",
				reason,
				refusal(
					`${t.matrix} is the approved matrix of ${sheet.operator_job}; cancel it first.`
				)
			);
			no_problems("D9-TRN-01, a second matrix of the job");

			open_form(MATRIX, t.matrix);
			button("Cancel").click();
			give_reason_if_asked(reason, (win) =>
				/Permanently Cancel/.test(win.cur_dialog?.$wrapper?.text() || "")
			);
			open_dialog().should("contain", "Permanently Cancel");
			cy.get(".modal.show .btn-primary").contains("Yes").click();
			cy.window().should((win) => expect(current(win).docstatus, "cancelled").to.equal(2));
			settle();
			primary_action().should("contain", "Amend").click();
			form_is(MATRIX, (doc) => doc.__islocal && doc.amended_from === t.matrix);
			settle();
			cy.window().then((win) => {
				made.count = rows(win, "items").length + 1;
				rows_up_to("items", made.count);
				fill_row("items", made.count, "controlled_document", t.instruction, "Link");
			});
			primary_action().should("contain", "Save").click();
			give_reason_if_asked(reason, (win) => !win.cur_frm.is_new());
			saved();
			cy.window().then((win) => {
				made.matrix = current(win).name;
			});
			sign("Approve", reason);
			cy.window().should((win) => {
				expect(current(win).docstatus, "approved").to.equal(1);
				expect(current(win).approved_by, "Approved By").to.equal(users[QA]);
			});
			signed_as("Approve", "Approved", reason);
			no_problems("D9-TRN-01, the amended matrix approved");

			open_list(RECORD, { controlled_document: t.instruction });
			cy.then(() => open_records_of(t.operator, t.revision, "New Assignment")).then(
				(opened) => {
					expect(opened, "the operator's training opened").to.have.length(1);
					cy.window().then((win) =>
						expect(opened[0].due_date, "due").to.equal(in_days(win, t.due_days))
					);
					listed().then((names) => expect(names).to.include(opened[0].name));
				}
			);
			cy.then(() => expect(t.due_days, "the days a new training is due in").to.equal(14));
			no_problems("D9-TRN-01");
		});
	});

	it("[URS-TRN-002] D9-TRN-02 The operator signs Trained and the Production Supervisor signs Competent", () => {
		prepare("prepare_training", { password, stage: "open" }).then((t) => {
			expect(
				t.reminders.filter((row) => row.status === "Open"),
				"the Training Due reminder"
			).to.have.length(1);
			sign_in(COORDINATOR);
			open_form(RECORD, t.record);
			cy.window().should((win) => {
				expect(current(win).status).to.equal("Open");
				expect(current(win).trigger).to.equal("New Assignment");
			});
			fill("training_method", "On-the-job", "Select");
			cy.window().then((win) => fill("training_date", today(win), "Date"));
			fill("trainer", "Production Supervisor (browser test D9)");
			fill("assessor", users[PRODUCTION], "Link");
			fill("assessment_method", "Practical Demonstration", "Select");
			save();
			no_problems("D9-TRN-02, the coordinator plans the training");

			sign_in(OPERATOR);
			open_form(RECORD, t.record);
			sign("Sign: Trained", null, "Trained");
			status_is("Trained", 0);
			signed_as("Trained", "Trained");
			no_problems("D9-TRN-02, the operator signs Trained");

			// What was planned is fixed once the trainee signed.
			sign_in(COORDINATOR);
			open_form(RECORD, t.record);
			fill("trainer", "Another trainer (browser test D9)");
			save_refused(refusal("Fixed once the trainee signed: Trainer."));
			no_problems("D9-TRN-02, the plan is fixed");

			sign_in(PRODUCTION);
			open_form(RECORD, t.record);
			sign("Competent", null, "Competent");
			status_is("Competent", 1);
			cy.window().should((win) =>
				expect(current(win).valid_until, "valid for the matrix row's 12 months").to.equal(
					in_months(win, today(win), 12)
				)
			);
			signed_as("Competent", "Competent");
			no_problems("D9-TRN-02, the supervisor signs Competent");

			prepare("run_daily_reminders", {
				category: "Training Due",
				reference_doctype: RECORD,
				reference_name: t.record,
			}).then((reminders) => {
				expect(reminders, "the record's reminders").to.not.be.empty;
				expect(
					reminders.filter((row) => row.status !== "Completed"),
					"no Training Due reminder left open"
				).to.be.empty;
			});
			no_problems("D9-TRN-02");
		});
	});

	it("[URS-TRN-002] [URS-TRN-006] D9-TRN-03 Not Competent opens retraining; signing without a plan is refused", () => {
		prepare("prepare_training", { password, stage: "planned_without_assessor" }).then((t) => {
			const reason = "Could not set the test pressure unaided";
			sign_in(OPERATOR);
			open_form(RECORD, t.record);
			sign_refused(
				"Sign: Trained",
				null,
				refusal("Name the assessor and how they will assess, before signing."),
				"Trained"
			);
			status_is("Open", 0);
			no_problems("D9-TRN-03, signing without an assessor");

			sign_in(COORDINATOR);
			open_form(RECORD, t.record);
			fill("training_method", "Read and Understand", "Select");
			save_refused(
				refusal(
					`Reading is not enough for ${t.instruction} in this job: it needs training and an assessment.`
				)
			);
			fill("training_method", "On-the-job", "Select");
			fill("assessor", users[PRODUCTION], "Link");
			fill("assessment_method", "Practical Demonstration", "Select");
			save();
			no_problems("D9-TRN-03, the full plan");

			sign_in(OPERATOR);
			open_form(RECORD, t.record);
			sign("Sign: Trained", null, "Trained");
			status_is("Trained", 0);

			sign_in(PRODUCTION);
			open_form(RECORD, t.record);
			sign("Not Competent", reason);
			status_is("Not Competent", 1);
			cy.window().should((win) => expect(current(win).outcome_reason).to.equal(reason));
			signed_as("Not Competent", "Rejected", reason);
			open_records_of(t.operator, t.revision, "Retraining").then((opened) =>
				expect(opened, "the retraining opened on the same revision").to.have.length(1)
			);
			no_problems("D9-TRN-03");
		});
	});

	it("[URS-TRN-006] [URS-TRN-005] D9-TRN-04 QA revokes a competence; the operator is refused at the job card", () => {
		prepare("prepare_revocation", { password }).then((t) => {
			const reason = "Nonconformity NC-test";
			sign_in(QA);
			open_list(RECORD, {
				status: "Competent",
				employee: t.operator,
				controlled_document: t.instruction,
			});
			listed().then((names) =>
				expect(names, "the operator's competence").to.include(t.record)
			);
			open_form(RECORD, t.record);
			sign("Revoke Competence", reason, "Revoke");
			status_is("Revoked", 1);
			cy.window().should((win) => {
				expect(current(win).revoked_by, "Revoked By").to.equal(users[QA]);
				expect(current(win).revoked_on?.slice(0, 10), "Revoked At").to.equal(today(win));
				expect(current(win).outcome_reason).to.equal(reason);
			});
			signed_as("Revoke", "Rejected", reason);
			open_records_of(t.operator, t.revision, "Retraining").then((opened) =>
				expect(opened, "the retraining opened").to.have.length(1)
			);
			no_problems("D9-TRN-04, QA revokes the competence");

			sign_in(OPERATOR);
			open_form("Job Card", t.job_card);
			reveal("time_logs");
			rows_up_to("time_logs", 1);
			// The time the operation was planned for (standard_flow.js).
			cy.window().then((win) => {
				const card = current(win);
				const planned = card.scheduled_time_logs[0];
				fill_row("time_logs", 1, "from_time", planned.from_time.slice(0, 19), "Datetime");
				fill_row("time_logs", 1, "to_time", planned.to_time.slice(0, 19), "Datetime");
				fill_row("time_logs", 1, "completed_qty", card.for_quantity);
			});
			fill_row("time_logs", 1, "employee", t.operator, "Link");
			save_refused(
				refusal(
					`Job card ${t.job_card}: work is logged only by trained, competent operators: ${t.operator} is not competent in ${t.instruction} revision ${t.revision_no}.`,
					"Operator not competent"
				)
			);
			stored("Job Card", t.job_card).then(
				(card) => expect(card.time_logs, "no time logged").to.be.empty
			);
			no_problems("D9-TRN-04");

			// The operator trained again, for the tests that log work on the journey's operations.
			prepare("restore_operator_training", { password });
		});
	});

	it("[URS-TRN-002] D9-TRN-05 QA records paper training history from the Training Record list", () => {
		prepare("prepare_training", { password, stage: "history" }).then((t) => {
			const made = {};
			const reason = "Paper record of 2025 transcribed";
			sign_in(QA);
			open_list(RECORD);
			add_record_button().click();
			open_dialog()
				.should("contain", "Record Training History")
				.and("contain", "For training given on paper before the system")
				.and("contain", "Employee")
				.and("contain", "Document");
			dialog_fill("employee", t.operator, "Link");
			dialog_fill("controlled_document", t.instruction, "Link");
			press_dialog("Start");
			form_is(RECORD, (doc) => !doc.__islocal && doc.controlled_document === t.instruction);
			settle();
			cy.window().should((win) => {
				const doc = current(win);
				made.record = doc.name;
				expect(doc.trigger, "Why").to.equal("Historical");
				expect(doc.status).to.equal("Open");
				expect(doc.employee).to.equal(t.operator);
				expect(doc.document_revision, "the revision in effect").to.equal(t.revision);
				expect(doc.due_date, "due today").to.equal(today(win));
				expect(doc.validity_months, "from the matrix row").to.equal(24);
				expect(doc.read_and_understand_allowed, "from the matrix row").to.equal(0);
			});

			fill("training_method", "Instructor-led", "Select");
			cy.window().then((win) => {
				made.trained_on = in_days(win, -200);
				fill("training_date", made.trained_on, "Date");
			});
			fill("trainer", "QA trainer (paper record)");
			save();
			sign_refused(
				"Record History",
				reason,
				refusal("Attach the training certificate, privately.")
			);

			reveal("certificate");
			attach("certificate", "training-certificate.pdf");
			save();
			sign("Record History", reason);
			status_is("Competent", 1);
			cy.window().should((win) => {
				expect(current(win).is_historical, "Entered From Paper").to.equal(1);
				expect(current(win).valid_until, "counted from the training date").to.equal(
					in_months(win, made.trained_on, 24)
				);
			});
			signed_as("Record History", "Verified", reason);
			stored(RECORD, t.record).then((opened) =>
				expect(opened.status, "the record the system had opened").to.equal("Withdrawn")
			);
			no_problems("D9-TRN-05, QA records the history");

			cy.then(() =>
				start_history_refused(
					t.operator,
					t.instruction,
					refusal(
						`${t.operator} is already competent on this revision (${made.record}).`
					)
				)
			);
			no_problems("D9-TRN-05");
		});
	});

	it("[URS-TRN-002] D9-TRN-06 Paper history is refused for oneself, for a document outside the job's matrix, and for anyone but QA", () => {
		prepare("prepare_training", { password, stage: "history_refusals" }).then((t) => {
			const made = {};
			sign_in(QA);
			start_history_refused(
				t.qa_employee,
				t.outside,
				refusal("Nobody records their own training history.")
			);
			start_history_refused(
				t.operator,
				t.outside,
				refusal(
					`History is entered for what the job is trained on: ${t.operator}'s approved matrix does not name ${t.outside}.`
				)
			);
			start_history(t.operator, t.instruction);
			form_is(RECORD, (doc) => !doc.__islocal && doc.trigger === "Historical");
			settle();
			cy.window().then((win) => {
				made.record = current(win).name;
			});
			cy.then(() =>
				start_history_refused(
					t.operator,
					t.instruction,
					refusal(`${made.record} already records this training history; complete it.`)
				)
			);

			cy.then(() => open_form(RECORD, made.record));
			fill("employee", t.qa_employee, "Link");
			save_refused(refusal("Set when the record was opened: Employee."));
			no_problems("D9-TRN-06, QA's refusals");

			sign_in(COORDINATOR);
			open_list(RECORD);
			settle();
			cy.get(`${FORM} .page-actions`).should(($actions) =>
				expect(
					$actions.find(".primary-action:visible").text(),
					"no Add Training Record button"
				).to.not.match(/\bAdd\b/)
			);
			refused_call("meddevice_suite.qms.training.start_history", {
				employee: t.operator,
				controlled_document: t.instruction,
			}).then(({ status, message }) => {
				expect(status, "a permission error").to.equal(403);
				expect(message).to.match(/^PermissionError/);
			});
			no_problems("D9-TRN-06");
		});
	});
});

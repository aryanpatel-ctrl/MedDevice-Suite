// Browser test sheet of data store D11, Task / Approval & Reminders (modules 48, 59, 60): work tasks
// with their signed approval, the price and purchase approval workflows, each person's My Work, and
// the reminders that track every due item until the work is done
// (meddevice_suite/work_management/work_task.py, reminders.py, my_work.py, setup/workflows.py,
// compliance/approvals.py). One test per line of the sheet, in its order, each signed in as the job
// the sheet names and done on the screens as that person would. Refusals are checked word for word;
// a refusal only an API call can reach is posted as that job.
//
// Since the sheet was written the code has changed, and the tests follow the code:
//   the reminder record type is "Due Reminder" (/desk/due-reminder), not "Reminder";
//   a Work Task reminder's lead time is the change action alert's (7 days until QA changes it).
//
// The sheet names the pilot's users and records; the test site has none, so the run uses the job
// users of the run and records of its own (meddevice_suite/qa/sheets/d11.py), new for each test, so
// no test depends on another:
//   the task of D11-T01 -> a task of the test's own, raised by the Production Supervisor for the
//   Operator with QA as approver, as far along as the test starts from (D11-T03 to T05);
//   second.qa@demo.example.com -> the run's second QA person;
//   RM-SILICONE, Silicone Compounds (Demo), City Hospital (Demo), FC-2W-SIL-16 -> the journey's
//   material, supplier, customer and product;
//   "wait for the next daily run" -> the daily run started by the set-up (`daily_run`), as the
//   scheduler starts it, for the categories the test is about.
// Alerts (the desk's bell) are made by a background job after the request commits: each is looked for
// for up to ALERT_TIMEOUT.

const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const SHEET = "meddevice_suite.qa.sheets.d11";
const COMPLETE_OCCURRENCE = "meddevice_suite.work_management.reminders.complete_occurrence";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const LINE = ".grid-row-open"; // a line of a table, opened in its own form
const TIMEOUT = 20000; // the longest a page or an action may take
const QUIET_MS = 300; // no request in flight for this long: the page has finished
const RELOAD_DELAY_MS = 1000; // the desk reloads a form this long after the server changes it
const ALERT_TIMEOUT = 180000; // an alert is made by a background job after the commit

const TASK = "Work Task";
const REMINDER = "Due Reminder";
const SCHEDULE = "Reminder Schedule";
const QUALITY_SETTINGS = "Quality Settings";
const STILL_OPEN = ["Open", "Overdue", "Escalated"];

const PRODUCTION = "Production Supervisor";
const OPERATOR = "Operator";
const QA = "QA Manager";
const MANAGEMENT = "Management";
const SALES = "Sales";
const PURCHASE = "Purchase";
const STORES = "Stores";

let password;
let users;
let data; // what the sheet works with (qa/sheets/d11.py prepare_sheet)
let TODAY; // the site's today, as the desk reads it

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

// The Administrator who runs the tests prepares what a test starts from (qa/sheets/d11.py).
function prepare(method, args = {}) {
	as_administrator();
	return cy.call(`${SHEET}.${method}`, args).then((r) => r.message);
}

// A session kept between tests is checked before it is used: one the server has since ended
// (found in Chrome, 2026-10-15: the Administrator's, late in the run) is signed in again.
function signed_in_as(user, pwd) {
	cy.session(
		["sheet-d11", user],
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

// A date is kept only once the person leaves its field ('change' on blur), and a form refresh set
// off by the field before (a fetch, a dependent field, ERPNext's own form script) can land in
// between and draw the box again from the still empty record: the person sees the box go empty
// and types the date again (found in CI, 2026-10-20, on a slow runner: the box empty in the
// screenshot). Typed again once only, and only when both the record and the box are empty: a box
// still showing a date the record did not take is a defect, left to the check that follows.
function retype_if_wiped(selector, value, label, read_stored, reopen = null) {
	settle();
	cy.window().then((win) => {
		const box = Cypress.$(win.document.body).find(selector).first().val();
		if (read_stored(win) || box) return;
		cy.log(`${label}: date wiped by a form refresh, typed again`);
		if (reopen) reopen();
		type_and_leave(selector, user_date(win, value));
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
	if (kind === "Date")
		retype_if_wiped(selector, value, fieldname, (win) => current(win)[fieldname]);
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
			close_open_rows(table);
		}
	});
	cy.window().should((win) => expect(rows(win, table)).to.have.length(count));
}

// A table whose lines are not edited in the grid (the maintenance checks, the qualification's
// tests) opens the line Add Row adds in its own form (Frappe's grid.js add_new_row), which lies
// fixed over the rest of the table (grid.scss .grid-row-open .form-in-grid) and covers the other
// lines (found in Chrome, 2026-10-16, D10-T12 and D10-T15). The person closes it before going on;
// closed as Frappe closes it, since the middle of its heading can land on Delete.
function close_open_rows(table) {
	cy.window().then((win) => {
		for (const row of win.cur_frm.fields_dict[table].grid.grid_rows || []) {
			if (row.wrapper?.hasClass("grid-row-open")) row.toggle_view(false);
		}
	});
	cy.get(LINE).should("not.exist");
	scrolled();
	settle();
}

// A line closing scrolls the page back to it with an animation; a click meanwhile lands on
// whatever the page has scrolled to.
function scrolled() {
	cy.window({ log: false }).should(
		(win) =>
			expect(win.$("html, body").is(":animated"), "the page has stopped scrolling").to.be
				.false
	);
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
	if (kind === "Date")
		retype_if_wiped(
			inputs_of(cell(table, idx, fieldname)),
			value,
			`line ${idx} ${fieldname}`,
			(win) => rows(win, table)[idx - 1][fieldname],
			() => cy.get(`${cell(table, idx, fieldname)}:first`).click()
		);
	cy.window().should((win) =>
		stored_as(rows(win, table)[idx - 1][fieldname], value, `line ${idx} ${fieldname}`)
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

// --- The signing dialog (compliance/esign.js) ----------------------------------------------------

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

function signatures_show(text) {
	settle();
	cy.get(`${FORM} .mds-signatures`).should("exist");
	cy.get(`${FORM} .mds-signatures`)
		.last()
		.then(($table) => {
			const pane = $table.closest(".tab-pane")[0];
			if (pane && !pane.classList.contains("active")) {
				cy.get(`${FORM} .form-tabs-list [aria-controls="${pane.id}"]`).click();
			}
		});
	cy.get(`${FORM} .mds-signatures`).last().scrollIntoView();
	cy.contains(`${FORM} .mds-signatures`, text).should("be.visible");
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

function stored_values(doctype, name, fieldname) {
	return cy
		.call("frappe.client.get_value", { doctype, filters: { name }, fieldname })
		.then((r) => r.message);
}

// --- The form's menus and buttons -------------------------------------------------------------

// The form's ⋯ menu, opened to read what it offers, then closed again. Its items are drawn in a
// dropdown of their own, outside the menu's button group; an item's label is followed by its
// keyboard shortcut, if it has one.
function form_menu_offers(label, expected) {
	settle();
	cy.get(`${FORM} .menu-btn-group > button:visible:first`).click();
	cy.get(".dropdown-menu.show").should("be.visible");
	cy.get(".dropdown-menu.show .menu-item-label").should(($labels) => {
		const labels = $labels.toArray().map((item) => item.textContent.trim());
		if (expected) expect(labels, "the menu").to.include(label);
		else expect(labels, "the menu").to.not.include(label);
	});
	cy.get(`${FORM} .menu-btn-group > button:visible:first`).click();
}

// The form offers no Save or Submit: the record is read-only for the person, or completed.
function nothing_to_save() {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	cy.get(`${FORM} .page-actions`).should(($actions) => {
		const labels = $actions
			.find(".primary-action:visible")
			.toArray()
			.map((action) => action.textContent.trim());
		expect(labels, "the form's main action").to.not.include("Save");
		expect(labels, "the form's main action").to.not.include("Submit");
	});
}

// --- The signing dialog (compliance/esign.js) ----------------------------------------------------

const STATEMENT = {
	Approved: "I approve this.",
	Verified: "I have verified this.",
	Reviewed: "I have reviewed this and found it fit for approval.",
	Rejected: "I reject this.",
};

// Opens the signing dialog of the act from its button, or from its group of buttons.
function open_signing(label, { act = label, group = null } = {}) {
	settle();
	if (group) from_menu(group, label);
	else button(label).click();
	opened_fully();
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
		expect(win.cur_dialog.title).to.equal(`Sign: ${act}`);
	});
}

// The dialog states what the signature means.
function statement_is(meaning) {
	cy.get(".modal.show").last().should("contain", `${meaning}: ${STATEMENT[meaning]}`);
}

// Signs the act with the run's password, and the reason when the act asks one; the form reloads
// the signed record (or opens the record the act opened).
function sign(label, { act = label, group = null, meaning = null, reason = null } = {}) {
	open_signing(label, { act, group });
	if (meaning) statement_is(meaning);
	if (reason) enter_reason(reason);
	enter_password();
	press_sign();
	cy.get(".modal.show").should("not.exist");
	settle(RELOAD_DELAY_MS + QUIET_MS);
}

// --- Signing in as a person who is not one of the jobs' users ---------------------------------

// The run's second QA person (qa/ui_journey.py ensure_second_qa), signed in as the jobs are.
function sign_in_as(email) {
	leave_page();
	signed_in_as(email, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", email);
}

// --- The sheet's records --------------------------------------------------------------------------

function open_single(doctype) {
	forget_unsaved();
	cy.visit(`/desk/${slug(doctype)}`);
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a form is open").to.equal("Form");
		expect(win.cur_frm?.doctype, "the form").to.equal(doctype);
	});
	settle();
}

// A record a line of My Work, or an alert, opens: its form, as the line's link opens it.
function open_record(doctype, name) {
	settle();
	cy.window().then((win) => win.frappe.set_route("Form", doctype, name));
	form_is(doctype, (doc) => doc.name === name);
	settle();
}

// The person fills in a new task on its form, not yet saved.
function task_form({ subject, assigned_to, approver, description = data.task_description }) {
	new_form(TASK);
	fill("subject", subject);
	cy.window().then((win) => fill("due_date", in_days(win, 3), "Date"));
	fill("assigned_to", assigned_to, "Link");
	fill("approver", approver, "Link");
	fill("description", description);
}

// A prompt the form opens (frappe.prompt): the person types into its own field and presses its own
// primary button; the form reloads the record the call changed.
function answer_prompt(fieldname, text) {
	open_dialog();
	cy.get(`.modal.show textarea[data-fieldname="${fieldname}"]`).last().click();
	cy.get(`.modal.show textarea[data-fieldname="${fieldname}"]`).last().clear();
	cy.get(`.modal.show textarea[data-fieldname="${fieldname}"]`).last().type(text);
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value(fieldname)).to.equal(text));
	cy.get(".modal.show .btn-modal-primary:visible").last().click();
	cy.get(".modal.show").should("not.exist");
	settle(RELOAD_DELAY_MS + QUIET_MS);
}

// Every field of the form is shown read-only to the person.
function every_field_read_only() {
	settle();
	cy.window().should((win) => {
		const writable = win.cur_frm.fields
			.filter(
				(field) => field.df && !win.frappe.model.no_value_type.includes(field.df.fieldtype)
			)
			.filter((field) => field.disp_status === "Write")
			.map((field) => field.df.fieldname);
		expect(writable, "fields the person may change").to.be.empty;
	});
}

// --- The approval workflows (setup/workflows.py) ---------------------------------------------------

function workflow_actions() {
	settle();
	return cy.get(`${FORM} .actions-btn-group`);
}

function take_action(action) {
	workflow_actions().find("> button").click();
	cy.get(`${FORM} .actions-btn-group .dropdown-menu a.dropdown-item`)
		.contains(new RegExp(`^\\s*${action}\\s*$`))
		.click();
	// The approval engine's save makes the desk reload the form a second later (standard_flow.js).
	settle(RELOAD_DELAY_MS + QUIET_MS);
}

// The state the form's title shows (Frappe's indicator, the workflow state).
function pill_is(state) {
	settle();
	cy.get(`${FORM} .page-head .indicator-pill:visible`).should(($pills) =>
		expect($pills.toArray().map((pill) => pill.textContent.trim())).to.include(state)
	);
}

// The record awaits its approver: the person who raised it is offered no workflow action, no Save
// and no Submit.
function nothing_offered_to_the_raiser() {
	workflow_actions().should("not.be.visible");
	cy.get(`${FORM} .page-actions .primary-action:visible`).should("not.exist");
}

// The first line of the record's items, opened in its own form, shows its rate read-only; closed
// again as Frappe closes it.
function lines_read_only() {
	settle();
	cy.get(
		`${FORM} .frappe-control[data-fieldname="items"] .grid-row[data-idx="1"] .row-index:first`
	).click();
	cy.get(LINE).should("be.visible");
	cy.get(`${LINE} .frappe-control[data-fieldname="rate"]`).should("exist");
	cy.get(`${LINE} .frappe-control[data-fieldname="rate"] input:visible`).should("not.exist");
	close_open_rows("items");
}

// --- My Work (work_management/my_work.py) ----------------------------------------------------------

function my_work() {
	return open_report("My Work");
}

function lines_about(lines, doctype, name) {
	return lines.filter((line) => line.doctype === doctype && line.name === name);
}

// My Work lists exactly these lines about the record ([] for none).
function my_work_lists(doctype, name, expected) {
	return cy
		.then(() => my_work())
		.then((lines) =>
			expect(
				lines_about(lines, doctype, name).map((line) => line.what),
				`My Work's lines about ${name}`
			).to.deep.equal(expected)
		);
}

// --- The daily run and the alerts it sends -----------------------------------------------------

// The daily reminder run, as the scheduler starts it (qa/sheets/d11.py daily_run); yields the
// reminders whose people it told something.
function daily_run(categories) {
	return prepare("daily_run", { categories }).then((ran) => ran.told);
}

// The reminders of the category about the record, as the signed-in person reads them.
function reminders_about(category, doctype, name, statuses = null) {
	const filters = { category, reference_doctype: doctype, reference_name: name };
	if (statuses) filters.status = ["in", statuses];
	return cy
		.call("frappe.client.get_list", {
			doctype: REMINDER,
			filters,
			fields: ["name", "subject", "status", "responsible_user", "notified", "completion"],
			order_by: "creation asc",
			limit_page_length: 0,
		})
		.then(({ message }) => message);
}

// The one open reminder of the category about the record.
function open_reminder(category, doctype, name) {
	return reminders_about(category, doctype, name, STILL_OPEN).then((rows) => {
		expect(rows, `one open ${category} reminder of ${name}`).to.have.length(1);
		return rows[0];
	});
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

function alert_subjects(win, doctype, name) {
	return win.frappe
		.xcall("frappe.client.get_list", {
			doctype: "Notification Log",
			filters: {
				document_type: doctype,
				document_name: name,
				for_user: win.frappe.session.user,
			},
			fields: ["subject"],
			limit_page_length: 0,
		})
		.then((rows) => rows.map((row) => plain(row.subject)));
}

// The signed-in person's alerts about the record, as they stand now.
function alerts_about(doctype, name) {
	return cy.window().then((win) => alert_subjects(win, doctype, name));
}

// The signed-in person's bell has, or comes to have, an alert about the record whose subject is
// `subject`; the person opens the list of their alerts about it. Yields every subject about it.
function alerted_about(doctype, name, subject) {
	const pattern = subject instanceof RegExp ? subject : new RegExp(`^${exact(subject).source}$`);
	let subjects = [];
	cy.window().then({ timeout: ALERT_TIMEOUT + 10000 }, (win) =>
		poll(
			() =>
				alert_subjects(win, doctype, name).then((found) => {
					subjects = found;
					return found.some((each) => pattern.test(each));
				}),
			ALERT_TIMEOUT
		).then(
			(found) =>
				expect(found, `${win.frappe.session.user} is alerted: ${subject}`).to.be.true
		)
	);
	open_list("Notification Log", { document_name: name });
	cy.window().should((win) =>
		expect(win.cur_list.data, "the bell's alerts about it").to.have.length.at.least(1)
	);
	return cy.then(() => subjects);
}

// Of the subjects, how many match.
function count_matching(subjects, pattern) {
	return subjects.filter((each) => pattern.test(each)).length;
}

function whole(text) {
	return new RegExp(`^${exact(text).source}$`);
}

// --- The sheet ------------------------------------------------------------------------------

context("D11 Task / Approval & Reminders: the browser test sheet, as each job", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password }));
		cy.then(() => cy.call(`${SHEET}.prepare_sheet`, { password })).then(({ message }) => {
			data = message;
		});
		cy.window().then((win) => {
			TODAY = win.frappe.datetime.get_today();
		});
	});

	// D11-T17 shortens the site's lot expiry lead time, which other sheets read: put back as
	// decided whatever became of the test.
	after(() => {
		as_administrator();
		cy.call(`${SHEET}.restore_lead_time`, { password });
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

	it("[URS-PLT-016] D11-T01 Production Supervisor raises a task for an operator with QA as approver", () => {
		const made = {};
		sign_in(PRODUCTION);
		task_form({
			subject: data.task_subject,
			assigned_to: users[OPERATOR],
			approver: users[QA],
		});
		save();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.status, "Status").to.equal("Open");
			expect(doc.name, "named TASK-YYYY-NNNNN").to.match(
				new RegExp(`^TASK-${TODAY.slice(0, 4)}-\\d{5}$`)
			);
			expect(doc.due_date, "Due").to.equal(in_days(win, 3));
			expect(doc.result || null, "What Was Done").to.equal(null);
			made.task = doc.name;
		});
		no_problems("D11-T01, the Production Supervisor");

		sign_in(OPERATOR);
		cy.then(() => my_work_lists(TASK, made.task, [`Do the task: ${data.task_subject}`]));
		cy.then(() => open_record(TASK, made.task));
		offered("Submit for Approval", true);
		no_problems("D11-T01");
	});

	it("[URS-PLT-016] D11-T02 A task cannot be approved by the person assigned or given to a shared account", () => {
		sign_in(PRODUCTION);
		task_form({
			subject: data.task_subject,
			assigned_to: users[OPERATOR],
			approver: users[OPERATOR],
		});
		save_refused(refusal("The approver is not the person assigned."));
		// The lookup never offers the Administrator (Frappe's user list leaves the shared accounts
		// out), so the person cannot choose it; it is entered as data, and the save is refused.
		cy.window().then((win) =>
			win.frappe.model.set_value(TASK, current(win).name, "approver", "Administrator")
		);
		settle();
		cy.window().should((win) => expect(current(win).approver).to.equal("Administrator"));
		save_refused(refusal("Approver is a person's own active account."));
		cy.window().should((win) => expect(win.cur_frm.is_new(), "not saved").to.be.ok);
		no_problems("D11-T02");
	});

	it("[URS-PLT-016] D11-T03 Only the person assigned says what was done; nothing changes while submitted", () => {
		prepare("prepare_task", { password, stage: "open" }).then(({ task, subject }) => {
			sign_in(PRODUCTION);
			open_form(TASK, task);
			fill("result", "done");
			save_refused(refusal("Only the person assigned says what was done."));
			no_problems("D11-T03, the supervisor's result");

			sign_in(OPERATOR);
			open_form(TASK, task);
			fill("subject", `${subject} (changed)`);
			save_refused(refusal(`Only whoever raised ${task}, or QA, changes what it is.`));
			no_problems("D11-T03, the operator's title");

			// Reloaded: the refused change is gone; the operator submits the task.
			open_form(TASK, task);
			cy.window().should((win) => expect(current(win).subject).to.equal(subject));
			button("Submit for Approval").click();
			answer_prompt("result", "Die cleaned, no damage found");
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.status, "Status").to.equal("Submitted");
				expect(doc.result, "What Was Done").to.equal("Die cleaned, no damage found");
				expect(String(doc.submitted_on).slice(0, 10), "Submitted On").to.equal(TODAY);
			});
			offered("Submit for Approval", false);
			no_problems("D11-T03, the operator submits");

			sign_in(PRODUCTION);
			open_form(TASK, task);
			cy.window().then((win) => fill("due_date", in_days(win, 5), "Date"));
			save_refused(
				refusal(
					`${task} is submitted; it changes only as it is approved, returned or withdrawn.`
				)
			);
			stored_values(TASK, task, ["status", "due_date"]).then((values) => {
				expect(values.status).to.equal("Submitted");
				cy.window().then((win) => expect(values.due_date).to.equal(in_days(win, 3)));
			});
			no_problems("D11-T03");
		});
	});

	it("[URS-PLT-016] D11-T04 The approver returns the task with a signed reason and it is done again", () => {
		const reason = "Record the inspection of the die lip as well";
		prepare("prepare_task", { password, stage: "submitted" }).then(({ task, subject }) => {
			sign_in(QA);
			my_work_lists(TASK, task, [`Approve the task: ${subject}`]);
			open_record(TASK, task);
			open_signing("Return");
			statement_is("Rejected");
			enter_reason(reason);
			enter_password();
			press_sign();
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.status, "Status").to.equal("Returned");
				expect(doc.return_reason, "Returned Because").to.equal(reason);
				expect(doc.docstatus, "not closed").to.equal(0);
			});
			signed_as("Return", "Rejected");
			signatures_show(reason);
			no_problems("D11-T04, QA returns it");

			sign_in(OPERATOR);
			my_work_lists(TASK, task, [`Do the task again: ${subject}`]);
			open_record(TASK, task);
			button("Submit for Approval").click();
			answer_prompt("result", "Die cleaned; die lip inspected, no nicks");
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.status, "Status").to.equal("Submitted");
				expect(doc.result, "What Was Done").to.equal(
					"Die cleaned; die lip inspected, no nicks"
				);
				expect(doc.return_reason || null, "Returned Because").to.equal(null);
			});
			no_problems("D11-T04");
		});
	});

	it("[URS-PLT-016] D11-T05 The approver approves the task with a signature, which closes it", () => {
		const made = {};
		prepare("prepare_task", { password, stage: "resubmitted" }).then(({ task }) => {
			made.task = task;
		});
		// Due in three days: the daily run raises its Work Task reminder, with its approver now.
		cy.then(() => daily_run(["Work Task"]));
		cy.then(() => prepare("prepare_task", { password, stage: "open" })).then(({ task }) => {
			made.open_task = task;
		});
		cy.then(() => {
			sign_in(QA);
			open_reminder("Work Task", TASK, made.task).then((reminder) => {
				made.reminder = reminder.name;
				expect(reminder.responsible_user, "the approver answers for it").to.equal(
					users[QA]
				);
			});
			open_form(TASK, made.task);
			sign("Approve", { meaning: "Approved", reason: "Work verified" });
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.status, "Status").to.equal("Approved");
				expect(doc.approved_by, "Approved By").to.equal(users[QA]);
				expect(String(doc.approved_on).slice(0, 10), "Approved On").to.equal(TODAY);
				expect(doc.docstatus, "submitted, final").to.equal(1);
			});
			signed_as("Approve", "Approved");
			for (const label of ["Withdraw", "Approve", "Return"]) offered(label, false);
			form_menu_offers("Delete", false);
			refused_call("frappe.client.delete", { doctype: TASK, name: made.task });
			no_problems("D11-T05, QA approves it");

			// No path deletes a task, the Administrator's neither: an approved one is submitted, and a
			// task not yet approved refuses deletion itself.
			as_administrator();
			refused_call("frappe.client.delete", { doctype: TASK, name: made.task });
			refused_call("frappe.client.delete", { doctype: TASK, name: made.open_task }).should(
				"match",
				exact("A task is never deleted; withdraw it instead.")
			);
			for (const name of [made.task, made.open_task]) {
				stored_values(TASK, name, ["name"]).then((values) =>
					expect(values?.name, `${name} is still there`).to.equal(name)
				);
			}

			// The next daily run completes its reminder.
			daily_run(["Work Task"]);
			sign_in(QA);
			reminders_about("Work Task", TASK, made.task).then((rows) =>
				expect(rows.map((row) => [row.name, row.status, row.completion])).to.deep.equal([
					[
						made.reminder,
						"Completed",
						`Work Task ${made.task} no longer shows this due.`,
					],
				])
			);
			no_problems("D11-T05");
		});
	});

	it("[URS-PLT-016] D11-T06 Whoever raised a task withdraws it with a reason; others may not", () => {
		const made = {};
		sign_in(PRODUCTION);
		task_form({
			subject: data.task_subject,
			assigned_to: users[OPERATOR],
			approver: users[QA],
		});
		save();
		cy.window().then((win) => {
			made.task = current(win).name;
		});
		no_problems("D11-T06, the supervisor raises it");

		sign_in(OPERATOR);
		cy.then(() => open_form(TASK, made.task));
		offered("Submit for Approval", true); // the form has drawn its buttons
		offered("Withdraw", false);
		no_problems("D11-T06, the operator");

		sign_in(PRODUCTION);
		cy.then(() => open_form(TASK, made.task));
		button("Withdraw").click();
		answer_prompt("reason", "Die replaced instead");
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.status, "Status").to.equal("Withdrawn");
			expect(doc.withdrawn_reason, "Withdrawn Because").to.equal("Die replaced instead");
		});
		cy.then(() => my_work_lists(TASK, made.task, []));
		no_problems("D11-T06, the supervisor withdraws it");

		for (const job of [OPERATOR, QA]) {
			sign_in(job);
			cy.then(() => my_work_lists(TASK, made.task, []));
		}
		no_problems("D11-T06");
	});

	it("[URS-PLT-016] D11-T07 A task is visible only to those involved, QA and Management", () => {
		const made = {};
		prepare("prepare_task", { password, stage: "open" }).then(({ task }) => {
			made.others = task;
		});
		// Beside it, a task given to Sales: Sales sees the tasks it is involved in.
		cy.then(() =>
			prepare("prepare_task", { password, stage: "open", assigned_to: SALES })
		).then(({ task }) => {
			made.own = task;
		});
		const listed = (names) =>
			cy
				.call("frappe.client.get_list", {
					doctype: TASK,
					filters: { name: ["in", names] },
					fields: ["name"],
					limit_page_length: 0,
				})
				.then(({ message }) => message.map((row) => row.name).sort());
		cy.then(() => {
			sign_in(SALES);
			open_list(TASK, { name: made.others });
			cy.window().should(
				(win) =>
					expect(win.cur_list.data, "the supervisor's task for the operator").to.be.empty
			);
			listed([made.others, made.own]).should("deep.equal", [made.own]);
			no_problems("D11-T07, Sales");

			sign_in(MANAGEMENT);
			open_list(TASK, { name: made.others });
			cy.window().should((win) =>
				expect(win.cur_list.data.map((row) => row.name)).to.deep.equal([made.others])
			);
			listed([made.others, made.own]).should("deep.equal", [made.others, made.own].sort());
			no_problems("D11-T07");
		});
	});

	it("[URS-PLT-014] D11-T08 Each job sees its own reminders; reminders cannot be made or changed by hand", () => {
		const made = {};
		prepare("prepare_lot_expiring", { password, days: 5 }).then((lot) => {
			made.lot = lot.lot;
		});
		cy.then(() => prepare("prepare_calibration_due", { password })).then((instrument) => {
			made.asset = instrument.asset;
		});
		cy.then(() => daily_run(["Lot Expiry", "Calibration Due"]));
		cy.then(() => {
			open_reminder("Lot Expiry", "Batch", made.lot).then((row) => {
				made.lot_reminder = row.name;
			});
			open_reminder("Calibration Due", "Asset", made.asset).then((row) => {
				made.calibration_reminder = row.name;
			});
		});
		const visible = () =>
			cy
				.call("frappe.client.get_list", {
					doctype: REMINDER,
					filters: { name: ["in", [made.lot_reminder, made.calibration_reminder]] },
					fields: ["name", "category"],
					limit_page_length: 0,
				})
				.then(({ message }) => message.map((row) => row.category).sort());
		cy.then(() => {
			sign_in(STORES);
			open_list(REMINDER);
			cy.get(`${FORM} .page-actions .primary-action:visible`).should("not.exist"); // no + Add
			visible().should("deep.equal", ["Lot Expiry"]);
			// Every reminder Stores sees is of the Stock User job, or names Stores.
			cy.call("frappe.client.get_list", {
				doctype: REMINDER,
				fields: ["name", "responsible_roles", "responsible_user"],
				limit_page_length: 0,
			}).then(
				({ message }) =>
					expect(
						message
							.filter(
								(row) =>
									!(row.responsible_roles || "")
										.split("\n")
										.includes("Stock User") &&
									row.responsible_user !== users[STORES]
							)
							.map((row) => row.name),
						"reminders of other jobs"
					).to.be.empty
			);

			open_form(REMINDER, made.lot_reminder);
			cy.window().should((win) => expect(current(win).category).to.equal("Lot Expiry"));
			every_field_read_only();
			nothing_to_save();
			offered("Mark Completed", false);
			no_problems("D11-T08, Stores");

			sign_in(QA);
			open_list(REMINDER);
			visible().should("deep.equal", ["Calibration Due", "Lot Expiry"]);
			no_problems("D11-T08");
		});
	});

	it("[URS-PLT-014] D11-T09 A system reminder is not ticked off by hand", () => {
		prepare("prepare_lot_expiring", { password, days: 5 }).then(({ lot }) => {
			daily_run(["Lot Expiry"]);
			sign_in(QA);
			open_reminder("Lot Expiry", "Batch", lot).then((reminder) => {
				open_form(REMINDER, reminder.name);
				offered("Mark Completed", false);
				// What the browser's console sends, as QA.
				refused_call(COMPLETE_OCCURRENCE, { name: reminder.name, remark: "done" }).should(
					"match",
					exact(`${reminder.name} is completed by the work in its record, not by hand.`)
				);
				stored_values(REMINDER, reminder.name, ["status", "completed_by"]).then(
					(values) => {
						expect(values.status, "Status").to.equal(reminder.status);
						expect(values.completed_by || null, "Completed By").to.equal(null);
					}
				);
			});
			no_problems("D11-T09");
		});
	});

	it("[URS-PLT-015] D11-T10 A Reminder Schedule must name a business job and a lead time within bounds", () => {
		sign_in(QA);
		new_form(SCHEDULE);
		fill("title", "Internal audit");
		fill("responsible_role", "System Manager", "Link");
		fill("frequency", "Half-yearly", "Select");
		cy.window().then((win) => fill("first_due_date", in_days(win, 20), "Date"));
		fill("lead_days", 7);
		save_refused(refusal("The responsible job is one of the business jobs' roles."));

		fill("responsible_role", "Quality Manager", "Link");
		fill("lead_days", 400);
		save_refused(refusal("Raise it between 0 and 365 days before it is due."));
		cy.window().should((win) => expect(win.cur_frm.is_new(), "not saved").to.be.ok);

		fill("lead_days", 7);
		save();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.active, "Active").to.equal(1);
			expect(doc.next_due_date, "Next Due").to.equal(in_days(win, 20));
			expect(doc.next_due_date, "Next Due = First Due").to.equal(doc.first_due_date);
			expect(win.cur_frm.get_field("next_due_date").df.read_only, "set by the system").to.be
				.ok;
		});
		no_problems("D11-T10");
	});

	it("[URS-PLT-015] D11-T11 QA completes a recurring occurrence and the schedule moves on", () => {
		const made = {};
		const remark = "Review held, minutes MR-2026-02";
		prepare("unique_tag").then((tag) => {
			made.title = `Management review ${tag}`;
		});
		cy.then(() => {
			sign_in(QA);
			new_form(SCHEDULE);
			fill("title", made.title);
			fill("responsible_role", "Quality Manager", "Link");
			fill("frequency", "Yearly", "Select");
			cy.window().then((win) => {
				made.first_due = in_days(win, 5);
				fill("first_due_date", made.first_due, "Date");
			});
			fill("lead_days", 7);
			save();
			cy.window().then((win) => {
				made.schedule = current(win).name;
			});
			no_problems("D11-T11, QA plans it");
		});

		cy.then(() => daily_run([]));
		cy.then(() => {
			sign_in(QA);
			cy.call("frappe.client.get_list", {
				doctype: REMINDER,
				filters: { schedule: made.schedule },
				fields: ["name", "category", "status", "subject", "due_date"],
				limit_page_length: 0,
			}).then(({ message }) => {
				expect(message, "one occurrence raised").to.have.length(1);
				const [reminder] = message;
				made.reminder = reminder.name;
				expect(reminder.category, "Category").to.equal("Recurring");
				expect(reminder.status, "Status").to.equal("Open");
				expect(reminder.due_date, "Due").to.equal(made.first_due);
			});
		});
		cy.then(() =>
			cy
				.window()
				.then((win) =>
					alerted_about(
						REMINDER,
						made.reminder,
						`${made.title} is due on ${user_date(win, made.first_due)}`
					)
				)
		);
		cy.then(() => {
			open_form(REMINDER, made.reminder);
			cy.window().should((win) => expect(current(win).schedule).to.equal(made.schedule));
			button("Mark Completed").click();
			answer_prompt("remark", remark);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.status, "Status").to.equal("Completed");
				expect(doc.completed_by, "Completed By").to.equal(users[QA]);
				expect(doc.completion, "How It Was Completed").to.equal(remark);
			});
			offered("Mark Completed", false);

			open_form(SCHEDULE, made.schedule);
			cy.window().should((win) =>
				expect(current(win).next_due_date, "Next Due").to.equal(
					win.frappe.datetime.add_months(made.first_due, 12)
				)
			);
			no_problems("D11-T11");
		});
	});

	it("[URS-PLT-015] D11-T12 A Reminder Schedule is never deleted; clearing Active stops it", () => {
		// Due in three days, raised a week before: active, the next daily run would raise it.
		prepare("prepare_schedule", {
			title: "Internal audit",
			first_due_in_days: 3,
			lead_days: 7,
		}).then(({ schedule }) => {
			sign_in(QA);
			open_form(SCHEDULE, schedule);
			form_menu_offers("Delete", false);
			refused_call("frappe.client.delete", { doctype: SCHEDULE, name: schedule });
			as_administrator();
			refused_call("frappe.client.delete", { doctype: SCHEDULE, name: schedule }).should(
				"match",
				exact("A recurring reminder is not deleted; clear Active to stop it.")
			);
			no_problems("D11-T12, deletion");

			sign_in(QA);
			open_form(SCHEDULE, schedule);
			fill("active", 0, "Check");
			save();
			cy.window().should((win) => expect(current(win).active, "Active").to.equal(0));

			daily_run([]);
			sign_in(QA);
			cy.call("frappe.client.get_list", {
				doctype: REMINDER,
				filters: { schedule },
				fields: ["name"],
				limit_page_length: 0,
			}).then(({ message }) => expect(message, "occurrences raised").to.be.empty);
			stored_values(SCHEDULE, schedule, ["active"]).then((values) =>
				expect(values.active, "still there, inactive").to.equal(0)
			);
			no_problems("D11-T12");
		});
	});

	it("[URS-CMP-020] D11-T13 A purchase order is issued only after Management's approval", () => {
		const made = {};
		sign_in(PURCHASE);
		new_form("Purchase Order");
		fill("supplier", data.supplier, "Link");
		cy.window().then((win) => fill("schedule_date", in_days(win, 7), "Date"));
		fill("set_warehouse", data.stores.quarantine, "Link");
		rows_up_to("items", 1);
		fill_row("items", 1, "item_code", data.material, "Link");
		fill_row("items", 1, "qty", 10);
		save();
		take_action("Send for Approval");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Awaiting Approval");
			expect(current(win).docstatus, "not submitted").to.equal(0);
			made.order = current(win).name;
		});
		pill_is("Awaiting Approval");
		nothing_offered_to_the_raiser();
		no_problems("D11-T13, Purchase sends it");

		sign_in(MANAGEMENT);
		cy.then(() => my_work_lists("Purchase Order", made.order, ["Approve the order"]));
		cy.then(() =>
			alerted_about(
				"Purchase Order",
				made.order,
				`Purchase order ${made.order} is waiting for your approval`
			)
		);
		cy.then(() => open_record("Purchase Order", made.order));
		take_action("Approve");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Approved");
			expect(current(win).docstatus, "submitted").to.equal(1);
		});
		pill_is("Approved");
		no_problems("D11-T13, Management approves it");

		sign_in(PURCHASE);
		cy.then(() =>
			alerted_about(
				"Purchase Order",
				made.order,
				`Purchase order ${made.order} was approved`
			)
		);
		no_problems("D11-T13");
	});

	it("[URS-CMP-019] D11-T14 A quotation's price is approved by Management, never by Sales", () => {
		const made = {};
		sign_in(SALES);
		new_form("Quotation");
		fill("party_name", data.customer, "Link");
		rows_up_to("items", 1);
		fill_row("items", 1, "item_code", data.product, "Link");
		fill_row("items", 1, "qty", 100);
		fill_row("items", 1, "rate", data.rate);
		save();
		take_action("Send for Price Approval");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Awaiting Price Approval");
			expect(current(win).docstatus).to.equal(0);
			made.quotation = current(win).name;
		});
		pill_is("Awaiting Price Approval");
		nothing_offered_to_the_raiser();
		lines_read_only();
		no_problems("D11-T14, Sales sends it");

		sign_in(MANAGEMENT);
		cy.then(() => open_form("Quotation", made.quotation));
		take_action("Return for Revision");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Draft");
			expect(current(win).docstatus).to.equal(0);
		});
		pill_is("Draft");
		no_problems("D11-T14, Management returns it");

		sign_in(SALES);
		cy.then(() =>
			alerted_about(
				"Quotation",
				made.quotation,
				`Quotation ${made.quotation} was returned for revision`
			)
		);
		cy.then(() => open_form("Quotation", made.quotation));
		fill_row("items", 1, "rate", data.rate - 1);
		save();
		take_action("Send for Price Approval");
		cy.window().should((win) =>
			expect(current(win).workflow_state).to.equal("Awaiting Price Approval")
		);
		no_problems("D11-T14, Sales revises it");

		sign_in(MANAGEMENT);
		cy.then(() => open_form("Quotation", made.quotation));
		take_action("Approve");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Approved");
			expect(current(win).docstatus, "submitted").to.equal(1);
			expect(current(win).items[0].rate, "the revised rate").to.equal(data.rate - 1);
		});
		pill_is("Approved");
		no_problems("D11-T14, Management approves it");

		sign_in(SALES);
		cy.then(() =>
			alerted_about("Quotation", made.quotation, `Quotation ${made.quotation} was approved`)
		);
		no_problems("D11-T14");
	});

	it("[URS-PLT-020] D11-T15 A reminder tells its person once when it is raised", () => {
		const made = {};
		prepare("prepare_lot_expiring", { password, days: 5 }).then((lot) => {
			made.lot = lot;
		});
		cy.then(() => {
			sign_in(PRODUCTION);
			task_form({
				subject: data.task_subject,
				assigned_to: users[OPERATOR],
				approver: users[QA],
			});
			save();
			cy.window().then((win) => {
				made.task = current(win).name;
				made.due = in_days(win, 3);
				made.notice = `Task ${made.task} ${data.task_subject} is due on ${user_date(
					win,
					made.due
				)}`;
			});
			no_problems("D11-T15, the supervisor raises it");
		});

		cy.then(() => daily_run(["Work Task", "Lot Expiry"])).then((told) => {
			made.first_told = told;
		});
		cy.then(() => {
			as_administrator();
			open_reminder("Work Task", TASK, made.task).then((reminder) => {
				made.reminder = reminder.name;
				expect(made.first_told, "the run told its people").to.include(reminder.name);
			});
		});

		// Step 3: the operator's one notice, which opens the reminder.
		cy.then(() => {
			sign_in(OPERATOR);
			alerted_about(REMINDER, made.reminder, made.notice).then((subjects) =>
				expect(count_matching(subjects, whole(made.notice)), "one notice").to.equal(1)
			);
			no_problems("D11-T15, the operator's bell");
		});

		// Step 4: QA, not named on it, was not told; it records what its person was told.
		cy.then(() => {
			sign_in(QA);
			alerts_about(REMINDER, made.reminder).should("be.empty");
			open_form(REMINDER, made.reminder);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.category, "Category").to.equal("Work Task");
				expect(doc.responsible_user, "Responsible Person").to.equal(users[OPERATOR]);
				expect(doc.notified, "People Told Of").to.equal("Raised");
			});
			no_problems("D11-T15, QA");
		});

		// Step 5: the daily run again tells nobody again.
		cy.then(() => daily_run(["Work Task", "Lot Expiry"])).then((told) =>
			expect(told, "told again").to.not.include(made.reminder)
		);
		cy.then(() => {
			sign_in(OPERATOR);
			alerts_about(REMINDER, made.reminder).then((subjects) =>
				expect(subjects, "the operator's notices").to.deep.equal([made.notice])
			);
		});

		// Step 6: Stores has the Lot Expiring Soon alert only; the Lot Expiry reminder, told by it,
		// tells nobody again.
		cy.then(() => {
			as_administrator();
			open_reminder("Lot Expiry", "Batch", made.lot.lot).then((reminder) => {
				made.lot_reminder = reminder.name;
				expect(reminder.notified, "People Told Of").to.equal("Raised");
			});
		});
		cy.then(() => {
			sign_in(STORES);
			cy.window().then((win) =>
				alerted_about(
					"Batch",
					made.lot.lot,
					`Lot ${made.lot.lot} expires on ${user_date(win, made.lot.expiry_date)}`
				)
			);
			cy.then(() => alerts_about(REMINDER, made.lot_reminder)).should("be.empty");
			no_problems("D11-T15");
		});
	});

	it("[URS-PLT-014] [URS-PLT-020] D11-T16 Escalation is told once, to QA, Management and the responsible person", () => {
		const made = {};
		// The precondition: a task for the operator due ten days ago, beyond the seven days of
		// Escalate After Overdue, still to be done.
		prepare("prepare_task", { password, stage: "open", due_in_days: -10 }).then((task) => {
			made.task = task;
		});
		cy.then(() => daily_run(["Work Task"]));
		cy.then(() => {
			sign_in(QA);
			open_reminder("Work Task", TASK, made.task.task).then((reminder) => {
				made.reminder = reminder.name;
			});
		});
		cy.then(() => {
			open_form(REMINDER, made.reminder);
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.status, "Status").to.equal("Escalated");
				expect(doc.escalated_on, "Escalated On").to.be.ok;
				expect(doc.notified, "People Told Of").to.equal("Escalated");
				made.overdue = `Overdue: Task ${made.task.task} ${
					made.task.subject
				} is due on ${user_date(win, made.task.due_date)}`;
			});
			no_problems("D11-T16, the reminder");
		});

		// Step 3: each of the three has one escalation.
		for (const job of [QA, MANAGEMENT, OPERATOR]) {
			cy.then(() => {
				sign_in(job);
				alerted_about(REMINDER, made.reminder, made.overdue).then((subjects) =>
					expect(
						count_matching(subjects, whole(made.overdue)),
						`${job}: one escalation`
					).to.equal(1)
				);
				no_problems(`D11-T16, ${job}'s bell`);
			});
		}

		// Step 4: the daily run again tells nobody again; the reminder stays escalated.
		cy.then(() => daily_run(["Work Task"])).then((told) =>
			expect(told, "told again").to.not.include(made.reminder)
		);
		cy.then(() => {
			sign_in(OPERATOR);
			alerts_about(REMINDER, made.reminder).then((subjects) =>
				expect(count_matching(subjects, whole(made.overdue)), "one escalation").to.equal(1)
			);
			stored_values(REMINDER, made.reminder, ["status"]).then((values) =>
				expect(values.status, "Status").to.equal("Escalated")
			);
			no_problems("D11-T16");
		});
	});

	// Changes the site's lot expiry lead time under change control, which other sheets read, and
	// spends a change request on it: a retry would start over from a site the failed attempt left
	// changed. The `after` hook puts the lead time back.
	it(
		"[URS-PLT-014] D11-T17 A shorter lead time does not complete a reminder whose work is still to be done",
		{ retries: 0 },
		() => {
			const reason = "Supplier notice: lot under investigation";
			prepare("prepare_shorter_lead", { password }).then((made) => {
				expect(made.reminder, "the open Lot Expiry reminder").to.be.ok;
				sign_in(QA);
				open_single(QUALITY_SETTINGS);
				cy.window().should((win) =>
					expect(current(win).lot_expiry_warning_days).to.equal(made.lead_days)
				);
				fill("lot_expiry_warning_days", 10);
				fill("change_request", made.change_request, "Link");
				save();
				cy.window().should((win) =>
					expect(current(win).lot_expiry_warning_days, "Lot Expiry Alert").to.equal(10)
				);
				no_problems("D11-T17, QA shortens the lead time");

				daily_run(["Lot Expiry"]);
				sign_in(QA);
				open_form(REMINDER, made.reminder);
				cy.window().should((win) =>
					expect(current(win).status, "Status").to.equal("Open")
				);

				// QA puts the lot on hold with a signature.
				open_form("Batch", made.lot);
				button("Decide Lot").click();
				form_is("Lot Release", (doc) => doc.__islocal && doc.batch === made.lot);
				settle();
				save();
				sign("Hold", { reason });
				cy.window().should((win) => expect(current(win).docstatus).to.equal(1));
				no_problems("D11-T17, QA holds the lot");

				daily_run(["Lot Expiry"]);
				sign_in(QA);
				open_form(REMINDER, made.reminder);
				cy.window().should((win) => {
					const doc = current(win);
					expect(doc.status, "Status").to.equal("Completed");
					expect(doc.completion, "How It Was Completed").to.equal(
						`Batch ${made.lot} no longer shows this due.`
					);
				});
				prepare("restore_lead_time", { password }).should("equal", made.lead_days);
				no_problems("D11-T17");
			});
		}
	);

	it("[URS-PLT-017] D11-T18 My Work lists each job's waiting signatures, and never the author's own approval", () => {
		prepare("prepare_my_work", { password }).then((made) => {
			const record = made.device_master_record;
			const returned = new RegExp(
				`^Lot ${exact(made.returned.lot).source}: decide on 2(\\.0+)? returned to ${
					exact(made.returned.store).source
				}$`
			);

			// Step 2: QA.
			sign_in(QA);
			my_work().then((lines) => {
				expect(
					lines_about(lines, "Quality Inspection Template", made.specification.name).map(
						(line) => line.what
					)
				).to.deep.equal(["Approve the specification"]);
				expect(
					lines_about(lines, "Device Master Record", record.name).map(
						(line) => line.what
					)
				).to.deep.equal([`Approve the device master record of ${record.item_name}`]);
				expect(
					lines_about(lines, "Batch", made.returned.lot).filter((line) =>
						returned.test(line.what)
					),
					"the goods sent back"
				).to.have.length(1);
				// Step 5: QA raised this change request; it is not asked to approve it.
				expect(
					lines_about(lines, "Change Request", made.change_request.name),
					"the change QA raised"
				).to.be.empty;
			});
			// Each line opens its record.
			open_record("Quality Inspection Template", made.specification.name);
			open_record("Device Master Record", record.name);
			open_record("Batch", made.returned.lot);
			no_problems("D11-T18, QA");

			// Step 3: the record its author signed is not theirs to approve.
			sign_in(PRODUCTION);
			my_work().then((lines) => {
				expect(lines_about(lines, "Device Master Record", record.name), "the record").to.be
					.empty;
				const records = lines.filter((line) => line.doctype === "Device Master Record");
				for (const line of records) {
					expect(line.what).to.match(/^Complete and sign as author: /);
				}
			});
			no_problems("D11-T18, the author");

			// Step 4: Management.
			sign_in(MANAGEMENT);
			my_work_lists("Quotation", made.quotation, ["Approve the price"]);
			open_record("Quotation", made.quotation);
			no_problems("D11-T18, Management");

			// The change request waits for another QA person's approval: the one who assessed it.
			sign_in_as(made.change_request.assessed_by);
			my_work_lists("Change Request", made.change_request.name, [
				`Approve: ${made.change_request.title}`,
			]);
			no_problems("D11-T18");
		});
	});
});

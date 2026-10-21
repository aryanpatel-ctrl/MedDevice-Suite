// Browser test sheet of data store D12, Audit Logs (box 9F): the audit trail every regulated record
// writes by itself, the reason asked before a cancel or a change after submission, QA's review and
// Verify Integrity, the electronic signatures, the anchors of the whole trail and what they find,
// sealed record archives, the retention schedule, Quality Settings under change control, and the
// permissions and configuration nobody changes on a site (meddevice_suite/compliance/audit.py,
// audit_reason.py, audit_timeline.py, esign.py, fingerprint.py, audit_anchor.py, archive.py,
// retention.py, quality_settings.py, access_policy.py, setup/roles.py, public/js/audit_trail.js).
// One test per line of the sheet, in its order, each signed in as the job the sheet names and done
// on the screens as that person would. Refusals are checked word for word; a refusal only an API
// call can reach is posted as that job.
//
// Since the sheet was written the code has changed where the sheet touches it, and the tests check
// what the code now does:
//   Verify Integrity lists each signature with what its verification found (Unchanged, Changed and
//   signed again, Changed since signing, Changed outside the audit trail) under "Signatures:"
//   (D12-T04); the chains are keyed (fingerprint version 2), which changes nothing on the screen;
//   permission rows made by hand are refused on every record type of the app, so Role Permissions
//   Manager is refused with "part of the validated configuration" before the row's flags are looked
//   at; the "written only by the system" refusal is reached by a row inserted through the API
//   (D12-T10, D12-T21);
//   a record archive's period is set once: on a saved archive the screen does not let the person
//   change it, and the server refuses the change through the API (D12-T15);
//   the anchor file lives in private/audit, which no File may name (not on the screen).
//
// The sheet names the pilot's records; the test site has none, so the run uses records of its own
// (meddevice_suite/qa/sheets/d12.py), new for each test, so no test depends on another:
//   the purchase order of D12-T01 -> a new order of the journey's material from the journey's
//   supplier for each test (Silicone Compounds (Demo) and RM-SILICONE are the pilot's);
//   the demo caliper's calibration -> an instrument of the test's own, calibrated by Maintenance;
//   a draft asset or Work Task QA signs -> an instrument of the test's own ready for QA's approval
//   (D12-T07), and a change request QA assessed with a signature, for QA to approve, so the new
//   signature is chained to the one before (D12-T08);
//   the archive of D12-T14 -> an archive QA requested of a day long past (D12-T15);
//   the record of D12-T22 -> a workstation of the sheet's own with four audit events.
// D12-T22 tampers with the audit trail as an attacker with database access would, on this test site
// only, and the set-up then puts the site back as the backup taken before would (qa/sheets/d12.py
// restore_audit_trail): without it every later check of the whole trail would report the removal.

const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const SHEET = "meddevice_suite.qa.sheets.d12";
const VERIFY_RECORD = "meddevice_suite.compliance.audit_timeline.verify_record";
const ANCHOR_NOW = "meddevice_suite.compliance.audit_anchor.anchor_now";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const TIMEOUT = 20000; // the longest a page or an action may take
const QUIET_MS = 300; // no request in flight for this long: the page has finished
const RELOAD_DELAY_MS = 1000; // the desk reloads a form this long after the server changes it
// Alerts, the login history of a refused signature and an archive are written by background jobs
// after the request commits; on a busy test site they can come well after the act.
const BACKGROUND_TIMEOUT = 180000;
const ARCHIVE_TIMEOUT = 300000;

const QA = "QA Manager";
const SYSADMIN = "System Administrator";
const MANAGEMENT = "Management";
const PURCHASE = "Purchase";
const SALES = "Sales";
const STORES = "Stores";

let password;
let users;
let data; // what the sheet works with (qa/sheets/d12.py prepare_sheet)
let TODAY; // the site's today, as the desk reads it

// What went wrong in the test: a refused or failed request, or an error message on screen, that
// the test did not expect. Every test ends by checking it is empty. `tolerated` are refusals a step
// expects any number of (a page the job may not open is refused by more than one request).
const run = { problems: [], expected: [], tolerated: [] };

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

// The Administrator who runs the tests prepares what a test starts from (qa/sheets/d12.py).
function prepare(method, args = {}) {
	as_administrator();
	return cy.call(`${SHEET}.${method}`, args).then((r) => r.message);
}

// A session kept between tests is checked before it is used: one the server has since ended (a
// user's sessions end when Frappe starts deleting the user, D12-T11) is signed in again.
function signed_in_as(user, pwd) {
	cy.session(
		["sheet-d12", user],
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
			} else if (!run.tolerated.some((pattern) => pattern.test(message))) {
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

// Looks for `check()` to come true, each look a request, until the timeout.
function poll(check, timeout = TIMEOUT) {
	const started = Date.now();
	return new Promise((resolve) => {
		const again = () =>
			check().then(
				(done) => {
					if (done) return resolve(true);
					if (Date.now() - started > timeout) return resolve(false);
					setTimeout(again, 1000);
				},
				() => resolve(false)
			);
		again();
	});
}

// Waits until no request has been in flight for quiet_ms, the screen is not frozen, and the open
// form is not about to reload (standard_flow.js).
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
	const indicator = dialog.indicator?.attr("class") || "";
	return {
		dialog,
		title: plain(dialog.$wrapper.find(".modal-title").html()),
		message: plain(dialog.$wrapper.find(".msgprint").html()),
		text: plain(
			`${dialog.$wrapper.find(".modal-title").html()}: ${dialog.$wrapper
				.find(".msgprint")
				.html()}`
		),
		// Frappe marks errors and refusals red and warnings orange.
		error: /\b(red|orange)\b/.test(indicator),
		green: /\bgreen\b/.test(indicator),
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
		run.tolerated.length = 0;
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

// The answer of a check (Verify Integrity, Verify Audit Trail, Verify) shown in a message:
// {title, message, green}, closed once read.
function answer_shown(title_pattern) {
	return cy
		.window()
		.should((win) => {
			const shown = message_dialog(win);
			expect(shown, "the check's answer").to.not.equal(null);
			expect(shown.title).to.match(title_pattern);
		})
		.then({ timeout: 10000 }, (win) => {
			const { title, message, green } = message_dialog(win);
			return close_message(win).then((closed) => {
				expect(closed, "the answer closes").to.be.true;
				return { title, message, green };
			});
		});
}

// A server call the signed-in user makes that must be refused; returns the refusal, with the
// HTTP status.
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

// A page the job may not open: the desk refuses it with `message` (setup/desk.py getpage), which
// may be the answer of more than one request.
function opening_refused(path, message) {
	cy.then(() => run.tolerated.push(/PermissionError/));
	forget_unsaved();
	cy.visit(path);
	refused_with(exact(message));
	cy.then(() => {
		run.tolerated.length = 0;
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

// The one record of a settings record type (Quality Settings, System Settings, Log Settings).
function open_single(doctype) {
	forget_unsaved();
	cy.visit(`/desk/${slug(doctype)}`);
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a form is open").to.equal("Form");
		expect(win.cur_frm?.doctype, "the form").to.equal(doctype);
		expect(win.cur_frm.doc.name, "the record shown").to.equal(doctype);
	});
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
	cy.window().should((win) => expect(win.cur_list.data, "the list's lines").to.be.an("array"));
}

// Opens a report with its filters set, as its menu link with the filters does; resolves with the
// lines it shows.
function open_report(name) {
	forget_unsaved();
	cy.visit(`/desk/query-report/${encodeURIComponent(name)}`);
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a report is open").to.equal("query-report");
		expect(win.frappe.query_report?.report_name, "the report").to.equal(name);
		expect(win.frappe.query_report.data, "the report's lines").to.be.an("array");
		expect(win.frappe.query_report.data.length, "the report's lines").to.be.greaterThan(0);
	});
	settle();
	return cy.window().then((win) => win.frappe.query_report);
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
// the form redraw it.
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
	// A form redrawn by what an earlier field brought can take the typing away with the old input,
	// or close a table cell again; the person opens the field again and types it once more.
	cy.get("body").then(($body) => {
		const $input = $body.find(selector).first();
		const listed = $body.find('[role="listbox"]:visible [role="option"]').length > 0;
		if (!listed || !$input.length || $input.val() !== value) {
			if (reopen) reopen();
			type_afresh(selector, value);
		}
	});
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

// Puts `value` into an input as a person does for a field of `kind`.
function enter($input, value, kind, selector, reopen = null) {
	cy.window().then((win) => {
		if (kind === "Link") choose(selector, value, reopen);
		else if (kind === "Select") cy.get(first_of(selector)).select(value);
		else if (kind === "Check") cy.get(first_of(selector))[value ? "check" : "uncheck"]();
		else if (kind === "Date") type_and_leave(selector, user_date(win, value));
		else type_and_leave(selector, String(value));
	});
}

// The first match of each part of a selector list, as one query Cypress can run again.
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
// off by the field before can land in between and draw the box again from the still empty record:
// the person sees the box go empty and types the date again (found in CI, 2026-10-20, on a slow
// runner). Typed again once only, and only when both the record and the box are empty.
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
	if (typeof value === "number") expect(Number(stored), label).to.be.closeTo(value, 1e-6);
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

// A line Add Row opens in its own form covers the other lines; the person closes it, as Frappe
// closes it.
function close_open_rows(table) {
	cy.window().then((win) => {
		for (const row of win.cur_frm.fields_dict[table].grid.grid_rows || []) {
			if (row.wrapper?.hasClass("grid-row-open")) row.toggle_view(false);
		}
	});
	cy.get(".grid-row-open").should("not.exist");
	settle();
}

function cell(table, idx, fieldname) {
	return `${FORM} .frappe-control[data-fieldname="${table}"] .grid-row[data-idx="${idx}"] [data-fieldname="${fieldname}"]`;
}

// A line of a table found by its own name rather than its number: a table can hold two lines with
// the same number (found in Chrome, 2026-10-21: Log Settings' Reminder and Activity Log, both 16).
function fill_named_row(table, row_name, fieldname, value) {
	const at = `${FORM} .frappe-control[data-fieldname="${table}"] .grid-row[data-name="${row_name}"] [data-fieldname="${fieldname}"]`;
	settle();
	close_dialogs();
	close_open_lists();
	cy.get(`${at}:first`).scrollIntoView();
	cy.get(`${at}:first`).click();
	cy.get(inputs_of(at)).first().should("have.focus");
	type_and_leave(inputs_of(at), String(value));
	cy.window().should((win) =>
		stored_as(
			rows(win, table).find((row) => row.name === row_name)[fieldname],
			value,
			`line ${row_name} ${fieldname}`
		)
	);
}

function fill_row(table, idx, fieldname, value, kind = "Data") {
	settle();
	close_dialogs();
	close_open_lists();
	cy.get(`${cell(table, idx, fieldname)}:first`).click();
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

function button(label) {
	settle();
	close_onboarding();
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

// The labels of a group of the form's buttons (View), none when the group is not there.
function group_labels(group) {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	return cy.get("body").then(($body) =>
		$body
			.find(
				`${FORM} .inner-group-button[data-label="${encodeURIComponent(
					group
				)}"] .dropdown-item`
			)
			.toArray()
			.map((item) => decodeURIComponent(item.getAttribute("data-label") || ""))
	);
}

function primary_action() {
	settle();
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

function press_submit() {
	primary_action().should("contain", "Submit").click();
	opened_fully();
	cy.get(".modal.show").last().should("contain", "Permanently Submit");
	cy.get(".modal.show .btn-primary").contains("Yes").click();
	cy.window().should((win) => expect(current(win).docstatus, "submitted").to.equal(1));
	settle();
}

// The current dialog has finished fading in.
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

// The form's ⋯ menu, opened to read what it offers, then closed again.
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

function from_form_menu(label) {
	settle();
	cy.get(`${FORM} .menu-btn-group > button:visible:first`).click();
	cy.get(".dropdown-menu.show")
		.contains(".dropdown-item", new RegExp(`^\\s*${label}(\\s|$)`))
		.click();
}

// The form offers no Save or Submit: the record is read-only for the person.
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

// Every field of the form that holds a value shows it read-only: the person may not write it.
function every_field_read_only() {
	cy.window().should((win) => {
		const frm = win.cur_frm;
		expect(frm.perm[0].write || 0, "write permission").to.equal(0);
		const layout = ["Section Break", "Column Break", "Tab Break", "HTML", "Button"];
		const writable = frm.fields
			.filter((field) => field.df.fieldname && !layout.includes(field.df.fieldtype))
			.filter((field) => field.disp_status === "Write")
			.map((field) => field.df.fieldname);
		expect(writable, "fields the person can write").to.be.empty;
	});
}

// A list offers no way to add a record: no Add button.
function no_add_on_list() {
	settle();
	cy.get(`${FORM} .page-actions`).should("exist");
	cy.get(`${FORM} .page-actions`).should(($actions) => {
		const labels = $actions
			.find(".primary-action:visible")
			.toArray()
			.map((action) => action.textContent.trim());
		expect(
			labels.filter((label) => /(^|\s|\+)Add\b/.test(label)),
			"an Add button on the list"
		).to.be.empty;
	});
}

// A workflow action from the form's Actions menu (setup/workflows.py).
function take_action(label) {
	settle();
	cy.get(`${FORM} .actions-btn-group > button:visible`).click();
	cy.get(".dropdown-menu.show")
		.contains(".dropdown-item", new RegExp(`^\\s*${label}\\s*$`))
		.click();
}

// --- The reason dialog (public/js/audit_reason.js) ----------------------------------------------

function reason_asked(title) {
	opened_fully();
	cy.window().should((win) => {
		expect(win.cur_dialog?.title, "the reason dialog").to.equal(title);
		expect(win.cur_dialog.display, "it has finished opening").to.equal(true);
	});
}

function press_continue() {
	cy.get(".modal.show:last .btn-primary").contains("Continue").click();
}

function give_reason(reason) {
	const field = '.modal.show:last textarea[data-fieldname="reason"]';
	cy.get(field).click();
	cy.get(field).clear();
	cy.get(field).type(reason);
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
	press_continue();
}

// Continue with no reason: Frappe's own dialog says the reason is missing, and nothing is sent.
function continue_without_reason() {
	press_continue();
	refused_with(/^Missing Values Required: Following fields have missing values: Reason$/);
}

// --- The signing dialog (public/js/esign.js) ----------------------------------------------------

const STATEMENT = {
	Approved: "I approve this.",
};
const WRONG_PASSWORD = "Incorrect password. The signature was not recorded.";

function open_signing(label, { act = label } = {}) {
	settle();
	button(label).click();
	opened_fully();
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
		expect(win.cur_dialog.title).to.equal(`Sign: ${act}`);
	});
}

function statement_is(meaning) {
	cy.get(".modal.show").last().should("contain", `${meaning}: ${STATEMENT[meaning]}`);
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

// The signatures esign.js adds to the form's dashboard show `text`; on a form with a Connections
// tab Frappe draws the dashboard in that tab, which the person opens to read them.
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

// The person is still signed in as the job: a refused signature does not end the session.
function still_signed_in(job) {
	cy.request({ url: "/api/method/frappe.auth.get_logged_user", log: false })
		.its("body.message")
		.should("eq", users[job]);
	cy.window().its("frappe.session.user").should("eq", users[job]);
}

// --- Reading what is stored, as the signed-in person ----------------------------------------

function stored(doctype, name) {
	return cy.call("frappe.client.get", { doctype, name }).then((r) => r.message);
}

function listed(doctype, filters, fields = ["name"], order_by = "creation asc") {
	return cy
		.call("frappe.client.get_list", {
			doctype,
			filters,
			fields,
			order_by,
			limit_page_length: 0,
		})
		.then((r) => r.message);
}

function count_of(doctype, filters = {}) {
	return cy.call("frappe.client.get_count", { doctype, filters }).then((r) => r.message);
}

function as_data(value) {
	return typeof value === "string" ? JSON.parse(value) : value || {};
}

// An audit event's change of a field of a line of a table: [old, new], or undefined.
function row_change(changes, table, fieldname) {
	for (const [changed_table, _row, fields] of changes.row_changed || []) {
		if (changed_table !== table) continue;
		const field = fields.find(([name]) => name === fieldname);
		if (field) return [field[1], field[2]];
	}
	return undefined;
}

function field_change(changes, fieldname) {
	const field = (changes.changed || []).find(([name]) => name === fieldname);
	return field ? [field[1], field[2]] : undefined;
}

// --- The audit trail on a record (public/js/audit_trail.js) ---------------------------------------

// View > Verify Integrity on the open form; the answer, closed once read.
function verify_integrity() {
	from_menu("View", "Verify Integrity");
	return answer_shown(/^Integrity (verified|problem found)$/);
}

// View > Audit Trail on the open form lists the record's audit events; resolves with them, oldest
// first, as the signed-in reviewer reads them.
function audit_trail_of(doctype, name) {
	from_menu("View", "Audit Trail");
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a list is open").to.equal("List");
		expect(win.cur_list?.doctype, "the list").to.equal("Audit Event");
		const filters = (win.cur_list.filters || []).map((filter) => [filter[1], filter[3]]);
		expect(filters, "filtered to the record").to.deep.include(["reference_doctype", doctype]);
		expect(filters, "filtered to the record").to.deep.include(["reference_name", name]);
	});
	settle();
	return listed("Audit Event", { reference_doctype: doctype, reference_name: name }, [
		"name",
		"action",
		"user",
		"reason",
		"changes",
		"previous_event",
		"event_at",
	]).then((events) =>
		cy
			.window()
			.should((win) =>
				expect(win.cur_list.data.length, "the events the list shows").to.equal(
					Math.min(events.length, win.cur_list.page_length || 20)
				)
			)
			.then(() => events)
	);
}

// --- The Audit Anchor list (compliance/doctype/audit_anchor/audit_anchor_list.js) ---------------

function list_button(label) {
	settle();
	return cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]:visible:first`);
}

// Anchor Now on the list; resolves with the anchor of the audit trail its green message names
// and its number of entries.
function anchor_now() {
	list_button("Anchor Now").click();
	cy.get(".desk-alert:visible").should("contain", "Audit Event: anchor ANC-");
	settle();
	return cy
		.get(".desk-alert:visible")
		.last()
		.invoke("text")
		.then((text) => {
			const anchored = /Audit Event: anchor (ANC-\d{4}-\d+), (\d+) entries/.exec(text);
			expect(anchored, `the anchor of the audit trail in "${text}"`).to.not.equal(null);
			return { text, anchor: anchored[1], entries: Number(anchored[2]) };
		});
}

function verify_audit_trail() {
	list_button("Verify Audit Trail").click();
	return answer_shown(/^Integrity (verified|problem found)$/);
}

// The alert of a finding has come for the signed-in person (a System Notification, made in the
// background after the request commits): it is looked for until it has come, then read in the
// person's Notification Log list, and the finding it names opened.
function alerted_of_finding(finding, subject) {
	cy.window().then({ timeout: BACKGROUND_TIMEOUT + 10000 }, (win) => {
		const user = win.frappe.session.user;
		const look = () =>
			win.frappe
				.xcall("frappe.client.get_list", {
					doctype: "Notification Log",
					filters: {
						for_user: user,
						document_type: "Audit Trail Finding",
						document_name: finding,
					},
					fields: ["name"],
					limit_page_length: 1,
				})
				.then((found) => found.length > 0);
		return poll(look, BACKGROUND_TIMEOUT).then(
			(found) => expect(found, `${user} is alerted of ${finding}`).to.be.true
		);
	});
	open_list("Notification Log", { document_name: finding });
	cy.window().should((win) => {
		const subjects = win.cur_list.data.map((row) => plain(row.subject));
		expect(subjects, "the alert in the person's notifications").to.include(subject);
	});
	open_form("Audit Trail Finding", finding);
	cy.window().should((win) => {
		const doc = current(win);
		expect(doc.finding, "Finding").to.be.ok;
		expect(doc.what_to_do, "What To Do").to.contain("Treat this as a data-integrity incident");
	});
	every_field_read_only();
}

// The finding of the audit trail of `kind` about `subject`, as the reviewer reads it.
function finding_of(kind, subject) {
	return listed("Audit Trail Finding", { trail: "Audit Event", kind, subject }, ["name"]).then(
		(found) => {
			expect(found, `the finding ${kind} ${subject}`).to.have.length(1);
			return found[0].name;
		}
	);
}

// --- The sheet ------------------------------------------------------------------------------

context("D12 Audit Logs: the browser test sheet, as each job", () => {
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
		// A run stopped in D12-T22 left the trail tampered with: put back first.
		cy.then(() => cy.call(`${SHEET}.restore_audit_trail`));
		cy.window().then((win) => {
			TODAY = win.frappe.datetime.get_today();
		});
	});

	// The test site is left as the run found it, whatever D12-T22 got to.
	after(() => {
		as_administrator();
		cy.call(`${SHEET}.restore_audit_trail`);
	});

	beforeEach(() => {
		run.problems.length = 0;
		run.expected.length = 0;
		run.tolerated.length = 0;
		watch_requests();
		// A desk call the server refuses rejects its promise with nothing, which some ERPNext form
		// scripts leave unhandled; the refused request itself is reported by watch_requests with the
		// server's message. Any other script error is a problem of the step.
		// Frappe's report page leaves the refusal of its report unhandled in the same way
		// (query_report.js, get_script), which Cypress words "An unknown error has occurred:
		// undefined" (found in Chrome, 2026-10-21, D12-T12).
		cy.on("uncaught:exception", (error, _runnable, promise) => {
			const message = error?.message;
			const nothing =
				message === undefined ||
				message === "undefined" ||
				/^An unknown error has occurred: undefined$/m.test(String(message));
			if (!(promise && nothing)) {
				run.problems.push(`script error: ${message}`);
			}
			return false;
		});
	});

	it("[URS-CMP-007] D12-T01 Every change to a regulated record is recorded automatically with old and new values", () => {
		const made = {};
		const by = () => data.names[PURCHASE];
		sign_in(PURCHASE);
		new_form("Purchase Order");
		fill("supplier", data.supplier, "Link");
		cy.window().then((win) => fill("schedule_date", in_days(win, 7), "Date"));
		fill("set_warehouse", data.quarantine, "Link");
		rows_up_to("items", 1);
		fill_row("items", 1, "item_code", data.material, "Link");
		fill_row("items", 1, "qty", 10);
		save();
		cy.window().then((win) => {
			made.order = current(win).name;
		});
		fill_row("items", 1, "qty", 12);
		save();
		no_problems("D12-T01, Purchase");

		cy.then(() => {
			sign_in(QA);
			open_form("Purchase Order", made.order);
			listed(
				"Audit Event",
				{ reference_doctype: "Purchase Order", reference_name: made.order },
				["name", "action", "user", "previous_event", "changes", "event_at", "event_hash"]
			).then((events) => {
				made.created = events.find((event) => event.action === "Created");
				made.updated = events.find(
					(event) =>
						event.action === "Updated" &&
						String(row_change(as_data(event.changes), "items", "qty")) === "10,12"
				);
				expect(made.created, "the Created event").to.be.ok;
				expect(made.updated, "the Updated event of the quantity").to.be.ok;
			});
		});

		// The timeline, newest first, each event with its Details link.
		cy.then(() => {
			const timeline = `${FORM} .new-timeline .timeline-item`;
			cy.get(`${FORM} .new-timeline`).scrollIntoView();
			cy.get(`${FORM} .new-timeline`)
				.should("contain", `Updated by ${by()}`)
				.and("contain", `Created by ${by()}`);
			cy.get(timeline).should(($items) => {
				const items = $items.toArray();
				const at = (name) =>
					items.findIndex((item) =>
						item.querySelector(`a[href*="/audit-event/${name}"]`)
					);
				const updated = at(made.updated.name);
				const created = at(made.created.name);
				expect(updated, "the Updated event on the timeline").to.be.at.least(0);
				expect(created, "the Created event on the timeline").to.be.at.least(0);
				expect(updated, "newest first").to.be.lessThan(created);
				expect(plain(items[updated].innerHTML)).to.contain(`Updated by ${by()}`);
				expect(plain(items[created].innerHTML)).to.contain(`Created by ${by()}`);
				const details = items[updated].querySelector(`a[href*="/audit-event/"]`);
				expect(details.textContent.trim(), "its link").to.equal("Details");
				expect(details.getAttribute("href")).to.match(
					new RegExp(`/desk/audit-event/${made.updated.name}$`)
				);
			});
		});

		// Details opens the event (its link is the site's full address, which the test opens on the
		// test's own address).
		cy.then(() => open_form("Audit Event", made.updated.name));
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.reference_doctype, "Document Type").to.equal("Purchase Order");
			expect(doc.reference_name, "Document").to.equal(made.order);
			expect(doc.action, "Action").to.equal("Updated");
			expect(doc.user, "User").to.equal(users[PURCHASE]);
			expect(doc.event_at, "Date and Time").to.be.ok;
			expect(String(row_change(as_data(doc.changes), "items", "qty")), "qty").to.equal(
				"10,12"
			);
			expect(doc.previous_event, "Previous Event").to.equal(made.created.name);
			expect(doc.event_hash, "Event Fingerprint").to.match(/^[0-9a-f]{64}$/);
		});
		nothing_to_save();
		every_field_read_only();

		cy.then(() => open_form("Audit Event", made.created.name));
		cy.window().should((win) => {
			const snapshot = as_data(current(win).changes).snapshot;
			expect(current(win).action, "Action").to.equal("Created");
			expect(snapshot?.name, "the whole order").to.equal(made.order);
			expect(snapshot.items, "with its item row").to.have.length(1);
			expect(snapshot.items[0].item_code).to.equal(data.material);
			expect(snapshot.items[0].qty).to.equal(10);
		});
		no_problems("D12-T01");
	});

	it("[URS-CMP-011] D12-T02 Cancelling an approved record needs a reason, which the audit trail keeps", () => {
		const reason = "Supplier cannot deliver";
		prepare("prepare_purchase_order", { password, stage: "draft" }).then(({ order }) => {
			sign_in(PURCHASE);
			open_form("Purchase Order", order);
			take_action("Send for Approval");
			cy.window().should((win) =>
				expect(current(win).workflow_state).to.equal("Awaiting Approval")
			);
			settle();
			no_problems("D12-T02, Purchase");

			sign_in(MANAGEMENT);
			open_form("Purchase Order", order);
			take_action("Approve");
			cy.window().should((win) => {
				expect(current(win).workflow_state).to.equal("Approved");
				expect(current(win).docstatus).to.equal(1);
			});
			settle();

			// Step 2: Continue with no reason.
			take_action("Cancel");
			reason_asked(`Reason for cancelling ${order}`);
			continue_without_reason();
			cy.window().should((win) => expect(current(win).workflow_state).to.equal("Approved"));

			// Step 3: the dialog closed with its cross.
			cy.get(".modal.show:last .btn-modal-close:visible").click();
			cy.get(".modal.show").should("not.exist");
			settle();
			stored("Purchase Order", order).then((stored_order) => {
				expect(stored_order.docstatus, "still approved").to.equal(1);
				expect(stored_order.workflow_state).to.equal("Approved");
			});

			// Step 4: with the reason.
			take_action("Cancel");
			reason_asked(`Reason for cancelling ${order}`);
			give_reason(reason);
			cy.window().should((win) => {
				expect(current(win).docstatus, "cancelled").to.equal(2);
				expect(current(win).workflow_state).to.equal("Cancelled");
			});
			no_problems("D12-T02, Management");

			sign_in(QA);
			open_form("Purchase Order", order);
			audit_trail_of("Purchase Order", order).then((events) => {
				const cancelled = events.filter((event) => event.action === "Cancelled");
				expect(cancelled, "the Cancelled event").to.have.length(1);
				expect(cancelled[0].user).to.equal(users[MANAGEMENT]);
				expect(cancelled[0].reason).to.equal(reason);
			});
			no_problems("D12-T02");
		});
	});

	it("[URS-CMP-011] D12-T03 A cancel through the API without a reason is refused", () => {
		prepare("prepare_purchase_order", { password, stage: "approved" }).then(({ order }) => {
			sign_in(MANAGEMENT);
			open_form("Purchase Order", order);
			cy.window().should((win) => expect(current(win).docstatus).to.equal(1));
			refused_call("frappe.client.cancel", { doctype: "Purchase Order", name: order }).then(
				(message) =>
					expect(message).to.match(
						exact(
							`Give a reason to cancel Purchase Order ${order}. It is recorded in the audit trail.`
						)
					)
			);
			open_form("Purchase Order", order);
			cy.window().should((win) => {
				expect(current(win).docstatus, "still submitted").to.equal(1);
				expect(current(win).workflow_state).to.equal("Approved");
			});
			no_problems("D12-T03");
		});
	});

	it("[URS-CMP-010] D12-T04 QA verifies a record's audit trail and signatures", () => {
		prepare("prepare_instrument", { password, calibrated: 1 }).then(({ calibration }) => {
			sign_in(QA);
			open_form("Calibration Record", calibration);
			verify_integrity().then((answer) => {
				expect(answer.title).to.equal("Integrity verified");
				expect(answer.green, "green").to.be.true;
				expect(answer.message).to.contain(
					`The audit trail and the signatures of ${calibration} are intact.`
				);
				// Since the sheet: each signature, and that the record still holds what it signed.
				expect(answer.message).to.contain("Signatures:");
				expect(answer.message).to.contain(
					`Complete (Verified), signed by ${data.names.Maintenance}`
				);
				expect(answer.message).to.contain("the record still holds what was signed.");
			});
			audit_trail_of("Calibration Record", calibration).then((events) => {
				const actions = events.map((event) => event.action);
				expect(actions, "its events").to.include("Created");
				expect(actions, "its events").to.include("Submitted");
			});
			no_problems("D12-T04");
		});
	});

	it("[URS-CMP-012] D12-T05 Only QA and the System Administrator review the audit trail", () => {
		prepare("prepare_purchase_order", { password, stage: "approved" }).then(({ order }) => {
			sign_in(MANAGEMENT);
			open_form("Purchase Order", order);
			group_labels("View").then((labels) => {
				expect(labels, "View").to.not.include("Audit Trail");
				expect(labels, "View").to.not.include("Verify Integrity");
			});
			cy.get(`${FORM} .new-timeline`).should("exist");
			cy.get(`${FORM} .new-timeline a[href*="/audit-event/"]`).should("not.exist");

			opening_refused("/desk/audit-event", "You are not permitted to open Audit Event.");

			refused_call(VERIFY_RECORD, { doctype: "Purchase Order", name: order }).then(
				(message) =>
					expect(message).to.match(
						exact("You cannot review the audit trail of Purchase Order.")
					)
			);
			no_problems("D12-T05");
		});
	});

	it("[URS-CMP-009] D12-T06 Audit events cannot be created, changed or deleted, even by the System Administrator", () => {
		const made = {};
		sign_in(SYSADMIN);
		open_list("Audit Event");
		no_add_on_list();
		cy.window().then((win) => {
			expect(win.cur_list.data.length, "audit events listed").to.be.greaterThan(0);
			made.event = win.cur_list.data[0].name;
		});

		// The list's Actions, for a chosen event: no Delete.
		cy.get(`${FORM} .list-row-container .list-row-checkbox:visible:first`).click();
		cy.get(`${FORM} .actions-btn-group > button:visible`).click();
		cy.get(".dropdown-menu.show").should("be.visible");
		cy.get(".dropdown-menu.show .dropdown-item").should(($items) => {
			const labels = $items.toArray().map((item) => item.textContent.trim());
			expect(labels, "the list's actions").to.not.include("Delete");
		});
		cy.get(`${FORM} .actions-btn-group > button:visible`).click();

		cy.then(() => open_form("Audit Event", made.event));
		cy.window().then((win) => {
			made.reason = current(win).reason || null;
		});
		nothing_to_save();
		every_field_read_only();
		form_menu_offers("Delete", false);

		cy.then(() =>
			refused_call("frappe.client.set_value", {
				doctype: "Audit Event",
				name: made.event,
				fieldname: "reason",
				value: "x",
			})
		).then((message) => expect(message, "a permission error").to.match(/PermissionError/));
		cy.then(() =>
			refused_call("frappe.client.insert", {
				doc: {
					doctype: "Audit Event",
					reference_doctype: "Item",
					reference_name: data.material,
					document_name_at_event: data.material,
					action: "Updated",
					user: users[QA],
					event_at: `${TODAY} 10:00:00`,
					event_hash: "x",
				},
			})
		).then((message) => expect(message, "a permission error").to.match(/PermissionError/));
		cy.then(() => stored("Audit Event", made.event)).then((event) =>
			expect(event.reason || null, "the event is unchanged").to.equal(made.reason)
		);
		count_of("Audit Event", { event_hash: "x" }).then((count) =>
			expect(count, "nothing recorded").to.equal(0)
		);
		no_problems("D12-T06, the System Administrator");

		// Past every permission the record itself refuses.
		cy.then(() =>
			prepare("refusals_past_permissions", { doctype: "Audit Event", name: made.event })
		).then((refused) =>
			expect(refused).to.deep.equal({
				insert: "An audit event is recorded only by the audit trail.",
				change: "An audit event cannot be changed.",
				delete: "An audit event cannot be deleted.",
			})
		);
		no_problems("D12-T06");
	});

	it("[URS-CMP-001] D12-T07 A wrong signing password records nothing and does not sign the person out", () => {
		prepare("prepare_instrument", { password }).then(({ asset }) => {
			const subject = `Electronic signature refused, wrong password: Approve on Asset ${asset}`;
			sign_in(QA);
			open_form("Asset", asset);
			open_signing("Approve Equipment", { act: "Approve" });
			statement_is("Approved");
			enter_reason("Received and checked (browser test D12)");
			enter_password(`${password}-wrong`);
			const wrong = refusal(WRONG_PASSWORD);
			expect_refusal(wrong.server);
			press_sign();
			refused_with(wrong.screen);
			cy.window().should((win) => {
				expect(win.cur_dialog?.title, "the dialog stays open").to.equal("Sign: Approve");
				expect(win.cur_dialog.display).to.equal(true);
				expect(
					win.cur_dialog.get_value("password") || "",
					"the password cleared"
				).to.equal("");
			});
			still_signed_in(QA);
			stored("Asset", asset).then((doc) =>
				expect(doc.mds_equipment_approval, "the record unchanged").to.equal("Draft")
			);
			close_signing();

			open_list("Electronic Signature", { reference_name: asset });
			cy.window().should((win) =>
				expect(win.cur_list.data, "no signature of the record").to.have.length(0)
			);
			no_problems("D12-T07, QA");

			sign_in(SYSADMIN);
			cy.window().then({ timeout: BACKGROUND_TIMEOUT + 10000 }, (win) =>
				poll(
					() =>
						win.frappe
							.xcall("frappe.client.get_list", {
								doctype: "Activity Log",
								filters: { user: users[QA], subject },
								fields: ["name"],
								limit_page_length: 1,
							})
							.then((found) => found.length > 0),
					BACKGROUND_TIMEOUT
				).then((found) => expect(found, "the login history has it").to.be.true)
			);
			open_list("Activity Log", { user: users[QA], status: "Failed" });
			cy.window().should((win) => {
				const subjects = win.cur_list.data.map((row) => plain(row.subject));
				expect(subjects, "QA's failed entries").to.include(subject);
			});
			no_problems("D12-T07");
		});
	});

	it("[URS-CMP-002] D12-T08 A signature records who, when, what and its meaning, and is chained to the one before", () => {
		prepare("prepare_change_awaiting_approval", { password }).then((made) => {
			const request = made.change_request;
			sign_in(QA);
			open_form("Change Request", request);
			open_signing("Approve");
			statement_is("Approved");
			enter_password();
			press_sign();
			cy.get(".modal.show").should("not.exist");
			settle(RELOAD_DELAY_MS + QUIET_MS);
			cy.window().should((win) => expect(current(win).status).to.equal("Approved"));

			// The record's Electronic signatures section.
			signatures_show(data.names[QA]);
			cy.get(`${FORM} .mds-signatures`)
				.last()
				.find("tbody tr")
				.should(($rows) => {
					const lines = $rows.toArray().map((row) => plain(row.innerHTML));
					const approval = lines.find((line) => /^Approved /.test(line));
					expect(approval, "the approval's line").to.be.ok;
					expect(approval).to.contain(data.names[QA]);
					expect(approval).to.match(/ Approve$/);
				});

			open_list("Electronic Signature", { reference_name: request });
			no_add_on_list();
			cy.window().then((win) => {
				const newest = win.cur_list.data[0]?.name;
				expect(newest, "the newest signature").to.be.ok;
				open_form("Electronic Signature", newest);
			});
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.reference_doctype, "Document Type").to.equal("Change Request");
				expect(doc.reference_name, "Document").to.equal(request);
				expect(doc.document_name_at_signing, "Document Name at Signing").to.equal(request);
				expect(doc.action, "Action").to.equal("Approve");
				expect(doc.meaning, "Meaning").to.equal("Approved");
				expect(doc.signer, "Signed By").to.equal(users[QA]);
				expect(doc.signer_name, "Signer Name").to.equal(data.names[QA]);
				expect(String(doc.signed_at).slice(0, 10), "Signed At").to.equal(TODAY);
				expect(doc.reason || null, "Reason: the act asks none").to.equal(null);
				expect(doc.record_hash, "Record Fingerprint").to.match(/^[0-9a-f]{64}$/);
				expect(doc.signature_hash, "Signature Fingerprint").to.match(/^[0-9a-f]{64}$/);
				expect(doc.previous_signature, "Previous Signature: QA's assessment").to.equal(
					made.assessment
				);
			});
			nothing_to_save();
			every_field_read_only();
			form_menu_offers("Delete", false);
			no_problems("D12-T08");
		});
	});

	it("[URS-CMP-005] D12-T09 Jobs outside QA and administration cannot open the signature and audit lists", () => {
		prepare("prepare_released_lot", { password }).then(({ release }) => {
			sign_in(STORES);
			opening_refused(
				"/desk/electronic-signature",
				"You are not permitted to open Electronic Signature."
			);
			opening_refused("/desk/audit-event", "You are not permitted to open Audit Event.");
			forget_unsaved();
			cy.visit("/desk");
			cy.get(".desktop-icon").should("have.length.greaterThan", 0);
			cy.get('.desktop-icon[data-id="Compliance"]').should("not.exist");

			open_form("Lot Release", release);
			signatures_show(data.names[QA]);
			cy.contains(`${FORM} .mds-signatures`, "Released").should("be.visible");
			no_problems("D12-T09");
		});
	});

	it("[URS-CMP-015] D12-T10 Nobody can give a role permission to write signatures or audit events", () => {
		const validated = refusal(
			"The permissions on Electronic Signature are part of the validated configuration and change only with a release of this app."
		);
		sign_in(SYSADMIN);
		forget_unsaved();
		cy.visit(`/desk/permission-manager/${encodeURIComponent("Electronic Signature")}`);
		cy.get(".perm-engine table").should("be.visible").and("contain", "Quality Manager");
		settle();
		cy.get(`${FORM} .page-actions .primary-action:visible`)
			.should("contain", "Add A New Rule")
			.click();
		opened_fully();
		cy.window().should((win) =>
			expect(win.cur_dialog.title).to.equal("Add New Permission Rule")
		);
		cy.get('.modal.show select[data-fieldname="role"]').select("Stock User");
		cy.window()
			.its("cur_dialog")
			.should((dialog) => {
				expect(dialog.get_value("parent")).to.equal("Electronic Signature");
				expect(dialog.get_value("role")).to.equal("Stock User");
			});
		expect_refusal(validated.server);
		cy.get(".modal.show .btn-primary").contains("Add").click();
		refused_with(validated.screen);
		count_of("Custom DocPerm", { parent: "Electronic Signature" }).then((count) =>
			expect(count, "no permission row added").to.equal(0)
		);

		// A row granting create, inserted through the API, as no screen can.
		cy.then(() =>
			refused_call("frappe.client.insert", {
				doc: {
					doctype: "Custom DocPerm",
					parent: "Electronic Signature",
					parenttype: "DocType",
					parentfield: "permissions",
					role: "Stock User",
					permlevel: 0,
					read: 1,
					create: 1,
				},
			})
		).then((message) =>
			expect(message).to.match(
				exact(
					"Electronic Signature records are written only by the system; no role may be given create on them."
				)
			)
		);
		count_of("Custom DocPerm", { parent: "Electronic Signature" }).then((count) =>
			expect(count, "nothing changed").to.equal(0)
		);
		no_problems("D12-T10");
	});

	it("[URS-CMP-006] D12-T11 A user who has acted is never renamed or deleted, only disabled", () => {
		const user = () => users[PURCHASE];
		sign_in(SYSADMIN);
		cy.then(() => open_form("User", user()));
		from_form_menu("Rename");
		opened_fully();
		cy.window().should((win) => expect(win.cur_dialog.title).to.equal(`Rename ${user()}`));
		cy.then(() => {
			const renamed = user().replace("@", "2@");
			cy.get('.modal.show input[data-fieldname="new_name"]').clear().type(renamed);
			cy.window()
				.its("cur_dialog")
				.should((dialog) => expect(dialog.get_value("new_name")).to.equal(renamed));
			const rename = refusal(
				`User IDs are permanent in the audit trail. Create a new account instead of renaming ${user()}.`
			);
			expect_refusal(rename.server);
			cy.get(".modal.show .btn-primary").contains("Rename").click();
			refused_with(rename.screen);
		});
		settle();
		no_problems("D12-T11, rename");

		cy.then(() => open_form("User", user()));
		from_form_menu("Delete");
		opened_fully();
		cy.get(".modal.show").last().should("contain", "Permanently delete");
		cy.then(() => {
			const deletion = refusal(
				`${user()} has acted on regulated records, so the user ID stays in the audit trail and cannot be deleted. Disable the account instead.`
			);
			expect_refusal(deletion.server);
			cy.get(".modal.show .btn-primary").contains("Yes").click();
			refused_with(deletion.screen);
		});
		cy.then(() => stored("User", user())).then((doc) => {
			expect(doc.name, "the user ID unchanged").to.equal(user());
			expect(doc.enabled, "still enabled").to.equal(1);
		});
		no_problems("D12-T11");
	});

	it("[URS-CMP-023] D12-T12 QA reads the retention period of every record type", () => {
		sign_in(QA);
		open_report("Retention Schedule").then((report) => {
			expect(
				report.columns.map((column) => column.label),
				"the columns"
			).to.deep.equal([
				"Category",
				"Record type",
				"Kept for (years)",
				"Counted from",
				"Legal basis",
			]);
			const lines = report.data;
			expect(
				lines.filter((line) => line.years !== 10),
				"every row ten years"
			).to.be.empty;
			const category = (record_type) =>
				lines.find((line) => line.record_type === record_type)?.category;
			for (const record_type of [
				"Audit Event",
				"Electronic Signature",
				"Audit Anchor",
				"Audit Trail Finding",
				"Activity Log",
				"Record Archive",
			]) {
				expect(category(record_type), record_type).to.equal("Compliance evidence");
			}
			expect(category("Sales Order"), "Sales Order").to.equal("Sales records");
			expect(category("Asset"), "Asset").to.equal("Equipment and personnel records");
		});
		no_problems("D12-T12, QA");

		sign_in(MANAGEMENT);
		opening_refused(
			`/desk/query-report/${encodeURIComponent("Retention Schedule")}`,
			"You don't have access to Report: Retention Schedule"
		);
		no_problems("D12-T12");
	});

	it("[URS-CMP-024] D12-T13 The login history cannot be cleared before ten years", () => {
		sign_in(SYSADMIN);
		open_single("Log Settings");
		cy.window().then((win) => {
			const row = rows(win, "logs_to_clear").find(
				(each) => each.ref_doctype === "Activity Log"
			);
			expect(row, "the Activity Log row").to.be.ok;
			expect(row.days, "kept for 3660 days now (compliance/retention.py)").to.equal(3660);
			fill_named_row("logs_to_clear", row.name, "days", 90);
		});
		save_refused(
			refusal("The login history is kept for 3660 days and cannot be cleared sooner.")
		);
		open_single("Log Settings");
		cy.window().should((win) => {
			const row = rows(win, "logs_to_clear").find(
				(each) => each.ref_doctype === "Activity Log"
			);
			expect(row.days, "kept as it was").to.equal(3660);
		});
		no_problems("D12-T13");
	});

	it("[URS-CMP-025] D12-T14 QA makes a sealed archive of a period and verifies it", () => {
		const made = {};
		sign_in(QA);
		new_form("Record Archive");
		fill("from_date", TODAY, "Date");
		cy.window().then((win) => fill("to_date", in_days(win, 1), "Date"));
		save_refused(refusal("The period cannot end in the future."));
		cy.window().should((win) => expect(win.cur_frm.is_new(), "nothing saved").to.be.ok);
		fill("to_date", TODAY, "Date");
		save();
		cy.window().then((win) => {
			const doc = current(win);
			expect(doc.name, "its name").to.match(/^ARC-\d{4}-\d{4,}$/);
			expect(doc.status, "Status").to.be.oneOf(["Queued", "Running", "Completed"]);
			made.archive = doc.name;
		});

		// Reloaded until completed: the archive is built in the background.
		cy.window().then({ timeout: ARCHIVE_TIMEOUT + 10000 }, (win) =>
			poll(
				() =>
					win.frappe
						.xcall("frappe.client.get_value", {
							doctype: "Record Archive",
							filters: { name: made.archive },
							fieldname: "status",
						})
						.then(({ status }) => {
							expect(status, "the archive").to.not.equal("Failed");
							return status === "Completed";
						}),
				ARCHIVE_TIMEOUT
			).then((done) => expect(done, "the archive is completed").to.be.true)
		);
		cy.then(() => open_form("Record Archive", made.archive));
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.status, "Status").to.equal("Completed");
			expect(doc.record_count, "Records Archived").to.be.greaterThan(0);
			expect(doc.completed_at, "Completed At").to.be.ok;
			expect(doc.archive_file, "Archive File").to.match(/\.zip$/);
			expect(doc.archive_hash, "Archive Fingerprint (SHA-256)").to.match(/^[0-9a-f]{64}$/);
		});
		button("Verify").click();
		answer_shown(/^Archive verified$/).then((answer) => {
			expect(answer.green, "green").to.be.true;
			expect(answer.message).to.equal(
				"Every file is exactly as it was when the archive was made."
			);
		});

		// The file downloaded as QA, then opened without the system.
		cy.window().then((win) =>
			cy.request({ url: current(win).archive_file, encoding: "binary" }).then((response) => {
				expect(response.status, "downloaded").to.equal(200);
				expect(response.body.slice(0, 2), "a ZIP file").to.equal("PK");
			})
		);
		no_problems("D12-T14, QA");
		cy.then(() => prepare("archive_contents", { archive: made.archive })).then((contents) => {
			expect(contents.files, "its files").to.include.members([
				"index.csv",
				"logins.csv",
				"manifest.json",
			]);
			expect(contents.record_file, "records/<record type>/<name>.json").to.match(
				/^records\/[a-z_]+\/.+\.json$/
			);
			expect(contents.record_keys, "a record's file").to.include.members([
				"record_type",
				"record",
				"audit_events",
				"electronic_signatures",
			]);
		});
		no_problems("D12-T14");
	});

	it("[URS-CMP-026] D12-T15 An archive cannot be changed or deleted", () => {
		const made = {};
		prepare("prepare_archive").then(({ archive }) => {
			made.archive = archive;
		});
		cy.window().then({ timeout: ARCHIVE_TIMEOUT + 10000 }, (win) =>
			poll(
				() =>
					win.frappe
						.xcall("frappe.client.get_value", {
							doctype: "Record Archive",
							filters: { name: made.archive },
							fieldname: "status",
						})
						.then(({ status }) => status === "Completed"),
				ARCHIVE_TIMEOUT
			).then((done) => expect(done, "the archive is completed").to.be.true)
		);
		// The file the archive's field names, as the Administrator reads it.
		cy.then(() => prepare("archive_contents", { archive: made.archive })).then((contents) => {
			made.file = contents.file.name;
		});
		cy.then(() => {
			sign_in(QA);
			open_form("Record Archive", made.archive);
			// The period is set once: the saved archive's form does not let QA change it.
			cy.window().should((win) => {
				expect(
					win.cur_frm.get_field("from_date").disp_status,
					"Records Changed From"
				).to.not.equal("Write");
				expect(
					win.cur_frm.get_field("to_date").disp_status,
					"Records Changed To"
				).to.not.equal("Write");
				made.file_url = current(win).archive_file;
				made.from_date = current(win).from_date;
			});
			form_menu_offers("Delete", false);

			// What the screen does not offer, through the API.
			cy.window().then((win) =>
				refused_call("frappe.client.set_value", {
					doctype: "Record Archive",
					name: made.archive,
					fieldname: "from_date",
					value: win.frappe.datetime.add_days(made.from_date, -1),
				}).then((message) =>
					expect(message).to.match(exact("A record archive cannot be changed."))
				)
			);
			refused_call("frappe.client.delete", {
				doctype: "Record Archive",
				name: made.archive,
			}).then((message) =>
				expect(message, "a permission error").to.match(/PermissionError/)
			);
			cy.then(() =>
				refused_call("frappe.client.delete", { doctype: "File", name: made.file })
			);
			cy.then(() =>
				refused_call("frappe.client.set_value", {
					doctype: "File",
					name: made.file,
					fieldname: "is_private",
					value: 0,
				})
			);
			cy.then(() => stored("Record Archive", made.archive)).then((archive) =>
				expect(archive.from_date, "unchanged").to.equal(made.from_date)
			);
			no_problems("D12-T15, QA");
			cy.then(() => prepare("archive_contents", { archive: made.archive })).then(
				(contents) => {
					expect(contents.file.name, "the archive's file").to.equal(made.file);
					expect(contents.file.is_private, "still private").to.equal(1);
					expect(contents.file.file_url, "where it was").to.equal(made.file_url);
				}
			);

			cy.then(() =>
				prepare("refusals_past_permissions", {
					doctype: "Record Archive",
					name: made.archive,
				})
			).then((refused) =>
				expect(refused).to.deep.equal({
					change: "A record archive cannot be changed.",
					delete: "A record archive cannot be deleted.",
				})
			);
			no_problems("D12-T15");
		});
	});

	it("[URS-PLT-012] D12-T16 Quality Settings stay within bounds and change only under an approved change request", () => {
		prepare("prepare_quality_settings", { password }).then((made) => {
			sign_in(QA);
			open_single("Quality Settings");
			cy.window().should((win) =>
				expect(current(win).version, "Version").to.equal(made.version)
			);
			fill("lot_expiry_warning_days", 400);
			save_refused(refusal("Lot Expiry Alert is between 1 and 365."));
			fill("lot_expiry_warning_days", 20);
			cy.window().should((win) =>
				expect(current(win).change_request || null, "no change request").to.equal(null)
			);
			save_refused(
				refusal(
					"Quality Settings change only under an approved change request that lists them.",
					"Change control"
				)
			);
			open_single("Quality Settings");
			cy.window().should((win) => {
				expect(current(win).version, "Version unchanged").to.equal(made.version);
				expect(
					current(win).lot_expiry_warning_days,
					"Lot Expiry Alert unchanged"
				).to.equal(30);
			});
			no_problems("D12-T16");
		});
	});

	it("[URS-PLT-013] D12-T17 QA changes a setting under an approved change request; the version is raised and audited", () => {
		prepare("prepare_quality_settings", { password }).then((made) => {
			const request = made.change_request;
			expect(made.values.login_lockout_attempts, "Wrong Passwords Before Lockout").to.equal(
				5
			);
			sign_in(QA);
			open_single("Quality Settings");
			fill("login_lockout_attempts", 4);
			fill("change_request", request, "Link");
			save();
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.version, "Version up by one").to.equal(made.version + 1);
				expect(doc.changed_under, "Last Changed Under").to.equal(request);
				expect(doc.effective_from, "Effective From").to.equal(TODAY);
				expect(doc.change_request || null, "Change Request empty again").to.equal(null);
				expect(doc.login_lockout_attempts).to.equal(4);
			});
			no_problems("D12-T17, the change");

			// Another value, under the same request: the lookup no longer offers it, so QA types it.
			fill("lot_expiry_warning_days", 31);
			settle();
			reveal("change_request");
			type_and_leave(
				inputs_of(`${FORM} .frappe-control[data-fieldname="change_request"]`),
				request
			);
			settle();
			close_open_lists();
			cy.window().then((win) => {
				if (current(win).change_request !== request) {
					win.frappe.model.set_value(
						"Quality Settings",
						"Quality Settings",
						"change_request",
						request
					);
				}
			});
			settle();
			cy.window().should((win) =>
				expect(current(win).change_request, "Change Request").to.equal(request)
			);
			save_refused(
				refusal(
					`Change request ${request} has already changed Quality Settings; each change has its own approved change request.`,
					"Change control"
				)
			);

			open_single("Quality Settings");
			audit_trail_of("Quality Settings", "Quality Settings").then((events) => {
				const changed = events.filter((event) =>
					field_change(as_data(event.changes), "login_lockout_attempts")
				);
				expect(changed, "the Updated event").to.have.length.at.least(1);
				const event = changed[changed.length - 1];
				const changes = as_data(event.changes);
				expect(event.action).to.equal("Updated");
				expect(field_change(changes, "login_lockout_attempts"), "5 -> 4").to.deep.equal([
					5, 4,
				]);
				expect(field_change(changes, "changed_under")?.[1], "the request").to.equal(
					request
				);
			});
			no_problems("D12-T17, QA");

			sign_in(SYSADMIN);
			open_single("System Settings");
			cy.window().should((win) =>
				expect(
					current(win).allow_consecutive_login_attempts,
					"Allow Consecutive Login Attempts"
				).to.equal(3)
			);
			no_problems("D12-T17");
		});
	});

	it("[URS-CMP-037] D12-T18 The System Administrator cannot loosen the access policy or change Quality Settings", () => {
		sign_in(SYSADMIN);
		open_single("System Settings");
		fill("session_expiry", "170:00");
		const loosened = refusal(
			"Session Expiry (idle timeout) is set by the access policy: in Quality Settings, under change control."
		);
		expect_refusal(loosened.server);
		primary_action().should("contain", "Save").click();
		// Refused, or else saved: either ends the save; a save that went through is the defect.
		cy.window().should(
			(win) =>
				expect(
					Boolean(message_dialog(win)) || !win.cur_frm.is_dirty(),
					"the save has ended"
				).to.be.true
		);
		cy.window().then((win) =>
			expect(
				message_dialog(win),
				"refused (none shown: the save went through, which the access policy guard must refuse)"
			).to.not.equal(null)
		);
		refused_with(loosened.screen);
		settle();
		close_dialogs();
		open_single("System Settings");
		cy.window().should((win) =>
			expect(current(win).session_expiry, "Session Expiry unchanged").to.not.equal("170:00")
		);
		open_single("Quality Settings");
		nothing_to_save();
		every_field_read_only();
		no_problems("D12-T18");
	});

	it("[URS-CMP-038] D12-T19 Nobody adds fields, form changes or scripts on the site", () => {
		sign_in(SYSADMIN);
		opening_refused("/desk/customize-form", "You are not permitted to open Customize Form.");

		open_list("Custom Field");
		no_add_on_list();
		cy.window().then((win) => {
			const field = win.cur_list.data[0]?.name;
			expect(field, "a custom field").to.be.ok;
			open_form("Custom Field", field);
		});
		nothing_to_save();
		cy.window().should((win) =>
			expect(win.cur_frm.perm[0].write || 0, "may not change it").to.equal(0)
		);

		open_list("Client Script");
		no_add_on_list();
		no_problems("D12-T19");
	});

	it("[URS-CMP-040] D12-T20 QA anchors the trail on request and verifies it", () => {
		const made = {};
		// Entries the trail has not anchored yet.
		prepare("prepare_trail_record");
		cy.then(() => {
			sign_in(QA);
			open_list("Audit Anchor");
			no_add_on_list();
			cy.window().should((win) => {
				const names = win.cur_list.data.map((row) => row.name);
				expect(names, "newest first").to.deep.equal([...names].sort().reverse());
			});

			anchor_now().then(({ text, anchor }) => {
				made.anchor = anchor;
				expect(text, "and the signatures").to.match(
					/Electronic Signature: anchor ANC-\d{4}-\d+, \d+ entries/
				);
			});
			cy.window().should((win) => {
				const shown = win.cur_list.data.map((row) => row.name);
				expect(shown, "the new anchor listed").to.include(made.anchor);
			});
			cy.then(() => stored("Audit Anchor", made.anchor)).then((anchor) =>
				expect(anchor.trigger, "Recorded").to.equal("On request")
			);

			cy.then(() => open_form("Audit Anchor", made.anchor));
			cy.window().should((win) => {
				const doc = current(win);
				expect(doc.ledger, "Trail").to.equal("Audit Event");
				expect(doc.head_entry, "Newest Entry").to.be.ok;
				expect(doc.head_fingerprint, "Newest Entry Fingerprint").to.match(
					/^[0-9a-f]{64}$/
				);
				expect(doc.head_created, "Newest Entry Recorded At").to.be.ok;
				expect(doc.entry_count, "Entries").to.be.greaterThan(0);
				expect(doc.anchored_at, "Anchored At").to.be.ok;
				expect(doc.trigger, "Recorded").to.equal("On request");
				expect(doc.anchor_hash, "Anchor Fingerprint").to.match(/^[0-9a-f]{64}$/);
				expect(win.cur_frm.get_field("previous_anchor"), "Previous Anchor").to.be.ok;
			});
			nothing_to_save();
			every_field_read_only();
			form_menu_offers("Delete", false);

			open_list("Audit Anchor");
			verify_audit_trail().then((answer) => {
				expect(answer.title).to.equal("Integrity verified");
				expect(answer.green, "green").to.be.true;
				expect(answer.message).to.equal(
					"No anchored entry of the audit trail or the signatures is missing."
				);
			});
			no_problems("D12-T20, QA");

			sign_in(SYSADMIN);
			open_list("Audit Anchor");
			list_button("Anchor Now").should("be.visible");
			list_button("Verify Audit Trail").should("be.visible");
			no_problems("D12-T20");
		});
	});

	it("[URS-CMP-040] [URS-CMP-042] D12-T21 Anchors and findings cannot be made, changed or deleted, and other jobs cannot anchor", () => {
		const made = {};
		const validated = refusal(
			"The permissions on Audit Anchor are part of the validated configuration and change only with a release of this app."
		);
		sign_in(SYSADMIN);
		listed("Audit Anchor", {}, ["name", "entry_count"], "creation desc").then((anchors) => {
			made.anchor = anchors[0];
		});
		cy.then(() => {
			// None yet on the site (D12-T20 not run): the System Administrator anchors first.
			if (made.anchor) return;
			open_list("Audit Anchor");
			anchor_now().then(({ anchor }) =>
				stored("Audit Anchor", anchor).then((doc) => {
					made.anchor = doc;
				})
			);
		});
		cy.then(() =>
			refused_call("frappe.client.set_value", {
				doctype: "Audit Anchor",
				name: made.anchor.name,
				fieldname: "entry_count",
				value: 1,
			})
		).then((message) => expect(message, "a permission error").to.match(/PermissionError/));
		cy.then(() =>
			refused_call("frappe.client.delete", {
				doctype: "Audit Anchor",
				name: made.anchor.name,
			})
		).then((message) => expect(message, "a permission error").to.match(/PermissionError/));
		cy.then(() => stored("Audit Anchor", made.anchor.name)).then((anchor) =>
			expect(anchor.entry_count, "nothing changed").to.equal(made.anchor.entry_count)
		);

		// Step 3: Create ticked for a role in Role Permissions Manager.
		forget_unsaved();
		cy.visit(`/desk/permission-manager/${encodeURIComponent("Audit Anchor")}`);
		const create =
			'.perm-engine input[type="checkbox"][data-ptype="create"][data-role="Quality Manager"]';
		cy.get(create).should("exist").and("not.be.checked");
		settle();
		expect_refusal(validated.server);
		cy.get(create).click();
		refused_with(validated.screen);
		// Frappe's page leaves the screen frozen after a refused change (permission_manager.js
		// unfreezes only when the change is made): the person reloads it.
		forget_unsaved();
		cy.visit(`/desk/permission-manager/${encodeURIComponent("Audit Anchor")}`);
		cy.get(create).should("exist").and("not.be.checked");
		count_of("Custom DocPerm", { parent: "Audit Anchor" }).then((count) =>
			expect(count, "no permission row added").to.equal(0)
		);
		cy.then(() =>
			refused_call("frappe.client.insert", {
				doc: {
					doctype: "Custom DocPerm",
					parent: "Audit Anchor",
					parenttype: "DocType",
					parentfield: "permissions",
					role: "Quality Manager",
					permlevel: 0,
					read: 1,
					create: 1,
				},
			})
		).then((message) =>
			expect(message).to.match(
				exact(
					"Audit Anchor records are written only by the system; no role may be given create on them."
				)
			)
		);
		no_problems("D12-T21, the System Administrator");

		// Past every permission the anchor and a finding refuse.
		cy.then(() =>
			prepare("refusals_past_permissions", {
				doctype: "Audit Anchor",
				name: made.anchor.name,
			})
		).then((refused) =>
			expect(refused).to.deep.equal({
				insert: "An audit anchor is recorded only by the audit trail's anchoring.",
				change: "An audit anchor cannot be changed.",
				delete: "An audit anchor cannot be deleted.",
			})
		);
		cy.then(() =>
			prepare("refusals_past_permissions", { doctype: "Audit Trail Finding" })
		).then((refused) =>
			expect(refused).to.deep.equal({
				insert: "An audit trail finding is recorded only by the audit trail's anchoring.",
			})
		);

		// Step 4: Sales.
		sign_in(SALES);
		opening_refused("/desk/audit-anchor", "You are not permitted to open Audit Anchor.");
		cy.get(`button[data-label="${encodeURIComponent("Anchor Now")}"]`).should("not.exist");
		cy.get(`button[data-label="${encodeURIComponent("Verify Audit Trail")}"]`).should(
			"not.exist"
		);
		refused_call(ANCHOR_NOW, {}).then((message) =>
			expect(message, "a permission error").to.match(/PermissionError/)
		);
		no_problems("D12-T21");
	});

	// Runs once: a retry would find this attempt's findings already recorded and alerted. The run
	// puts the trail back at its start and its end whatever happens (qa/sheets/d12.py).
	it(
		"[URS-CMP-040] [URS-CMP-042] D12-T22 Removing a record's newest audit entries is detected and alerted once (test site only)",
		{ retries: 0 },
		() => {
			const made = {};
			prepare("restore_audit_trail");
			cy.then(() => prepare("prepare_trail_record")).then((record) => {
				made.record = record;
			});

			// Step 1 and 2: anchored, then the record verifies.
			cy.then(() => {
				sign_in(QA);
				open_list("Audit Anchor");
				anchor_now();
				open_form(made.record.doctype, made.record.name);
				verify_integrity().then((answer) => {
					expect(answer.title).to.equal("Integrity verified");
					expect(answer.green, "green").to.be.true;
				});
				no_problems("D12-T22, step 2");
			});

			// Step 3: the record's two newest audit events deleted from the database.
			cy.then(() =>
				prepare("remove_audit_events", { name: made.record.name, newest: 2 })
			).then((removed) => {
				made.removed = removed;
				expect(removed.removed, "the two newest removed").to.deep.equal(
					made.record.events.slice(-2)
				);
				expect(
					removed.head_removed,
					"the newest entry of the whole trail when anchored is among them (nothing else recorded since)"
				).to.be.true;
			});
			cy.then(() => {
				const { anchor } = made.removed;
				made.newest_missing = `Audit Event: entry ${anchor.head_entry}, the newest when anchor ${anchor.name} was recorded, is missing: the newest entries have been removed.`;
				made.two_missing = `Audit Event: 2 of the ${anchor.entry_count} entries recorded up to anchor ${anchor.name} are missing.`;
				sign_in(QA);
				open_form(made.record.doctype, made.record.name);
				verify_integrity().then((answer) => {
					expect(answer.title).to.equal("Integrity problem found");
					expect(answer.green, "red").to.be.false;
					// The record's own chain shows no problem: the findings start with the whole trail's.
					expect(
						answer.message.startsWith("Across the whole audit trail:"),
						answer.message
					).to.be.true;
					expect(answer.message).to.contain(made.newest_missing);
					expect(answer.message).to.contain(made.two_missing);
				});
				no_problems("D12-T22, step 3");
			});

			// Step 4: the alerts, as QA and as the System Administrator.
			cy.then(() => {
				finding_of("Newest entry missing", made.removed.anchor.head_entry).then(
					(finding) => {
						made.newest_finding = finding;
					}
				);
				finding_of("Entries missing", "2").then((finding) => {
					made.two_finding = finding;
				});
			});
			cy.then(() => {
				for (const job of [QA, SYSADMIN]) {
					sign_in(job);
					alerted_of_finding(
						made.newest_finding,
						"Audit trail problem: Newest entry missing (Audit Event)"
					);
					cy.window().should((win) =>
						expect(current(win).finding).to.equal(made.newest_missing)
					);
					alerted_of_finding(
						made.two_finding,
						"Audit trail problem: Entries missing (Audit Event)"
					);
					no_problems(`D12-T22, step 4, ${job}`);
				}
			});

			// Step 5: anchored and verified again: still reported, no new finding nor alert.
			cy.then(() => {
				sign_in(QA);
				count_of("Audit Trail Finding").then((count) => {
					made.findings = count;
				});
				open_list("Audit Anchor");
				anchor_now();
				verify_audit_trail().then((answer) => {
					expect(answer.title).to.equal("Integrity problem found");
					expect(answer.message).to.match(
						/Audit Event: 2 of the \d+ entries recorded up to anchor ANC-\d{4}-\d+ are missing\./
					);
				});
				cy.then(() => count_of("Audit Trail Finding")).then((count) =>
					expect(count, "no new finding, so no new alert").to.equal(made.findings)
				);
				no_problems("D12-T22, step 5");
			});

			// Step 6: one more removed: a new alert for the new count.
			cy.then(() => prepare("remove_audit_events", { name: made.record.name, oldest: 1 }));
			cy.then(() => {
				sign_in(QA);
				open_list("Audit Anchor");
				anchor_now();
				finding_of("Entries missing", "3").then((finding) => {
					made.three_finding = finding;
				});
				cy.then(() =>
					alerted_of_finding(
						made.three_finding,
						"Audit trail problem: Entries missing (Audit Event)"
					)
				);
				no_problems("D12-T22, step 6");
			});

			// The test site put back as it was; the whole trail verifies again.
			cy.then(() => prepare("restore_audit_trail")).then((restored) =>
				expect(restored.restored, "the removed events put back").to.have.length(3)
			);
			cy.then(() => {
				sign_in(QA);
				open_list("Audit Anchor");
				verify_audit_trail().then((answer) =>
					expect(answer.title, "the trail is whole again").to.equal("Integrity verified")
				);
				no_problems("D12-T22");
			});
		}
	);

	it("[URS-CMP-041] [URS-CMP-011] [URS-CMP-008] D12-T23 A sales order is audited; Update Items asks for a reason first", () => {
		const made = {};
		const reason = "Customer ordered a second unit";
		sign_in(SALES);
		new_form("Sales Order");
		fill("customer", data.customer, "Link");
		cy.window().then((win) => fill("delivery_date", in_days(win, 14), "Date"));
		rows_up_to("items", 1);
		fill_row("items", 1, "item_code", data.product, "Link");
		fill_row("items", 1, "qty", 1);
		save();
		press_submit();
		cy.window().then((win) => {
			made.order = current(win).name;
		});

		// Step 2: the reason first.
		button("Update Items").click();
		cy.then(() => reason_asked(`Reason for changing ${made.order}`));
		continue_without_reason();
		cy.window().should((win) =>
			expect(win.cur_dialog?.title, "the reason is still asked").to.match(
				/^Reason for changing /
			)
		);
		give_reason(reason);
		opened_fully();
		cy.window().should((win) => expect(win.cur_dialog.title).to.equal("Update Items"));

		// Step 3: ERPNext's own dialog, Qty 2.
		const qty_cell =
			'.modal.show [data-fieldname="trans_items"] .grid-body .grid-row[data-idx="1"] [data-fieldname="qty"]';
		cy.get(`${qty_cell}:first`).click();
		cy.get(`${qty_cell} input:visible:first`).clear();
		cy.get(`${qty_cell} input:visible:first`).type("2");
		cy.get(`${qty_cell} input:visible:first`).blur();
		settle();
		// The dialog's table keeps what is typed in its rows; a cell redrawn while typing leaves the
		// row as it was, and the person enters the quantity again (as the line's data).
		cy.window().then((win) => {
			const row = win.cur_dialog.fields_dict.trans_items.df.data[0];
			if (Number(row.qty) !== 2) {
				cy.log("Qty not taken from the cell: entered as the line's data");
				row.qty = 2;
				win.cur_dialog.fields_dict.trans_items.grid.refresh();
			}
		});
		cy.window().should((win) =>
			expect(Number(win.cur_dialog.fields_dict.trans_items.df.data[0].qty), "Qty").to.equal(
				2
			)
		);
		cy.get(".modal.show:last .btn-primary").contains("Update").click();
		cy.window().should((win) => expect(current(win).items[0].qty, "Qty").to.equal(2));
		settle(RELOAD_DELAY_MS + QUIET_MS);

		// Step 4: cancel through the API with no reason.
		cy.then(() =>
			refused_call("frappe.client.cancel", { doctype: "Sales Order", name: made.order })
		).then((message) =>
			expect(message).to.match(
				exact(
					`Give a reason to cancel Sales Order ${made.order}. It is recorded in the audit trail.`
				)
			)
		);
		cy.then(() => stored("Sales Order", made.order)).then((order) =>
			expect(order.docstatus, "still submitted").to.equal(1)
		);
		no_problems("D12-T23, Sales");

		// Step 5: QA reads the order's audit trail.
		cy.then(() => {
			sign_in(QA);
			open_form("Sales Order", made.order);
			audit_trail_of("Sales Order", made.order).then((events) => {
				const by_sales = (action) =>
					events.filter(
						(event) => event.action === action && event.user === users[SALES]
					);
				expect(by_sales("Created"), "Created").to.have.length(1);
				expect(by_sales("Submitted"), "Submitted").to.have.length(1);
				const updates = by_sales("Updated after submission");
				expect(updates, "Updated after submission").to.have.length.at.least(1);
				// ERPNext's Update Items saves the order's line on its own before the order
				// (erpnext accounts_controller.update_child_qty_rate); the order's event still
				// records the line's old and new quantity (compliance/audit.py, URS-CMP-008).
				const update = updates.find(
					(event) => String(row_change(as_data(event.changes), "items", "qty")) === "1,2"
				);
				expect(update, "the update with the line's old and new quantity").to.be.ok;
				expect(update.reason, "its reason").to.equal(reason);
			});
			no_problems("D12-T23");
		});
	});
});

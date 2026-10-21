// The browser test sheet of data store D3 Item / BOM / Routing Master (URS-MST-001 to URS-MST-016),
// one test per row of the sheet, in its order, each signed in as the job the sheet names and done
// on the screens as that person does it: QA sets up items and their specifications and approves
// them with signatures; Production drafts bills of materials, workstations, operations and routings,
// which QA approves; the records that would use a draft master are refused; and a master in use
// changes only under an approved change request (master_data/item_master.py, bom.py, routing.py,
// specification.py, master_approval.py, defining_documents.py, versioning.py).
//
// A row is never tried again (its first attempt may have done part of its work). A row that builds
// on an earlier row's records names it (`needs`) and is not run when that row failed; every other
// row starts from records of its own, so one failure hides no other row. The job users come from meddevice_suite/qa/ui_users.py;
// what the sheet's pilot records stand for on the test site (FC-2W-SIL-14 and its raw materials,
// their specifications and default bill of materials, the receipts inspected against, the
// controlled documents the masters name) from meddevice_suite/qa/sheets/d3.py, named with the run's
// own suffix. Where the sheet's order leaves a row's precondition to a later row (D3-T05 needs the
// specification D3-T21 approves; D3-T22 needs the specification D3-T19 approves again in draft),
// the row makes its precondition itself, as the sheet describes it. D3-T26 and D3-T27 start from
// records of their own the set-up makes: QA reads the audit trail of an approved finished good
// changed under a change request and of its bill of materials, and an item, a bill of materials and
// a specification are never merged, deleted or cancelled, not even by the Administrator.

const USERS = "meddevice_suite.qa.ui_users";
const SHEET = "meddevice_suite.qa.sheets.d3";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const TIMEOUT = 20000; // the longest a page or an action may take
const SETUP_TIMEOUT = 300000; // the sheet's set-up issues documents and approvals with signatures
const QUIET_MS = 300; // no request in flight for this long: the page has finished

const QA = "QA Manager";
const PRODUCTION = "Production Supervisor";
const PURCHASE = "Purchase";
const QC = "QC Inspector";
const MANAGEMENT = "Management";

const SPECIFICATION = "Quality Inspection Template";
const PARAMETERS = "item_quality_inspection_parameter";
const DIMENSION = "Dimension (mm)";
const FIXED = "Fixed sample size";

const ITEM_DRAFT = "Not yet approved by QA, so used in no record.";
const ITEM_READY = `${ITEM_DRAFT} Ready for QA's approval.`;
const MASTER_DRAFT = "Not yet approved by QA, so it is not used yet.";
const MASTER_READY = `${MASTER_DRAFT} Ready for QA's approval.`;
const MASTER_APPROVED = "Approved by QA; what was approved changes under change control.";
const SPECIFICATION_APPROVED =
	"Approved by QA; inspections are made against it. A change returns it to draft until QA approves it again.";
const BOM_READY = "Ready for QA's approval.";
const BOM_APPROVED = "Approved by QA; which bill is in force changes under change control.";
const WRONG_PASSWORD = "Incorrect password. The signature was not recorded.";
const REPLACE_REFUSED =
	"A bill of materials is not replaced inside approved ones; make the changed one as a new bill of materials under an approved change request.";
const TEST_METHOD_TYPES = ["SOP", "Work Instruction", "External Document"];

let password;
let users;
let d; // what the sheet starts from (qa/sheets/d3.py prepare_sheet)
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
	cy.session(["sheet-d3", email], () => {
		cy.request({
			url: "/api/method/login",
			method: "POST",
			body: { usr: email, pwd: password },
			log: false,
		});
	});
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", email);
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

// A set-up call of the sheet, as the Administrator (qa/sheets/d3.py); its result.
function set_up(method, args = {}) {
	sign_in_as_administrator();
	return cy
		.window()
		.its("frappe.csrf_token")
		.then((csrf_token) =>
			cy.request({
				method: "POST",
				url: `/api/method/${SHEET}.${method}`,
				body: { ...args, password },
				headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrf_token },
				timeout: SETUP_TIMEOUT,
				log: false,
			})
		)
		.then((response) => response.body.message);
}

// An approved change request listing the record, raised by Production and approved by QA.
function approved_change(record_type, record) {
	return set_up("approve_change", { record_type, record });
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

// Waits until no request has been in flight for quiet_ms, the screen is not frozen, and the
// open form is not about to reload (standard_flow.js settle).
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
	return {
		title: plain(dialog.$wrapper.find(".modal-title").text()),
		text: plain(dialog.$wrapper.find(".msgprint").text()),
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

// The row went as expected: no error message on screen, no unexpected refusal, and every
// refusal the row expected has happened.
function no_problems(row) {
	settle();
	cy.window({ log: false }).then((win) => {
		const shown = message_dialog(win);
		if (shown?.error)
			run.problems.push(`the message "${shown.title}: ${shown.text}" is shown`);
		const problems = run.problems.splice(0);
		const missing = run.expected.splice(0);
		expect(problems, `${row}: problems\n${problems.join("\n")}\n`).to.be.empty;
		expect(missing.map(String), `${row}: refusals expected but not made`).to.be.empty;
	});
}

// The refusal is on screen saying `message` word for word, under `title` when the sheet names
// one; it is closed, as the person closes it.
function refused(message, title) {
	cy.window()
		.should((win) => {
			const shown = message_dialog(win);
			expect(shown, "a message on screen").to.not.equal(null);
			expect(shown.text).to.contain(message);
			if (title) expect(shown.title, "its title").to.equal(title);
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
	leave_page();
	cy.visit(`/desk/${slug(doctype)}/${encodeURIComponent(name)}`);
	form_is(doctype, (doc) => doc.name === name);
	settle();
}

function new_form(doctype) {
	leave_page();
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

function in_days(win, days) {
	return win.frappe.datetime.add_days(win.frappe.datetime.get_today(), days);
}

function same_value(stored, value, label) {
	if (typeof value === "number") expect(Number(stored), label).to.be.closeTo(value, 1e-6);
	else if (value === "" || value === null) expect(stored ?? "", label).to.equal("");
	else expect(stored, label).to.equal(value);
}

// Enters the values in the form's fields, one after the other as the form takes them, each
// fetching what it brings (an item's details, say) before the next.
function set(values) {
	cy.window().then((win) => win.cur_frm.set_value(values));
	settle();
	cy.window().should((win) => {
		for (const [field, value] of Object.entries(values)) {
			same_value(current(win)[field], value, field);
		}
	});
}

// Picks a link's value from the field's list, as the person does: types it, then clicks its entry.
// For a link the form also fills from another field (the item's HSN/SAC code from its item group),
// picked after that field.
function pick(fieldname, value) {
	settle();
	reveal(fieldname);
	cy.get(`${FORM} .frappe-control[data-fieldname="${fieldname}"] input:visible`)
		.first()
		.clear()
		.focus()
		.type(value, { delay: 0 });
	// Retried until the list shows the entry: the list is redrawn as the search results arrive.
	cy.contains('[role="listbox"]:visible [role="option"]', value).click();
	settle();
	cy.window().should((win) => expect(current(win)[fieldname], fieldname).to.equal(value));
}

function set_row(table, idx, values) {
	cy.window().then((win) => {
		const row = current(win)[table][idx - 1];
		return win.frappe.model.set_value(row.doctype, row.name, values);
	});
	settle();
	cy.window().should((win) => {
		const row = current(win)[table][idx - 1];
		for (const [field, value] of Object.entries(values)) {
			same_value(row[field], value, `${table} row ${idx}: ${field}`);
		}
	});
}

// Fills a line of the table, in the empty line the form offers or a new one, field by field.
function add_row(table, values) {
	const [first] = values[0];
	let idx;
	cy.window().then((win) => {
		const frm = win.cur_frm;
		let row = (frm.doc[table] || []).find((line) => !line[first]);
		if (!row) {
			row = frm.add_child(table);
			frm.refresh_field(table);
		}
		idx = row.idx;
	});
	cy.then(() => {
		for (const [field, value] of values) set_row(table, idx, { [field]: value });
	});
}

function row_of(win, table, field, value) {
	return (current(win)[table] || []).find((row) => row[field] === value);
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

// Save, refused on screen with `message` (and `title`); nothing is saved.
function save_refused(message, title) {
	expect_refusal(new RegExp(escaped(message.slice(0, 60))));
	primary_action().should("contain", "Save").click();
	refused(message, title);
	cy.window().should((win) => expect(win.cur_frm.is_dirty(), "still unsaved").to.be.ok);
}

function escaped(text) {
	return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
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

function no_submit() {
	settle();
	cy.get(`${FORM} .page-actions`).should("be.visible");
	primary_action().should("not.exist");
}

// The banner at the top of the form (Frappe's headline), which the form's refresh sets.
function banner_says(text) {
	cy.get(`${FORM} .form-message`).should(($banner) =>
		expect(plain($banner.text())).to.contain(text)
	);
}

// The signing dialog of a signed act (compliance/esign.js): the reason, then the password, each
// once the dialog has taken it, as a person fills it in; then Sign. A reason left empty is left.
function sign(label, reason, secret = password) {
	button(label).click();
	cy.get(".modal.show").should("have.css", "opacity", "1");
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
		expect(win.cur_dialog.title).to.equal("Sign: Approve"); // every approval is the act Approve
	});
	if (reason) {
		cy.get('.modal.show textarea[data-fieldname="reason"]').click().type(reason);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
	}
	cy.get('.modal.show input[data-fieldname="password"]').click().type(secret, { log: false });
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("password")).to.equal(secret));
	cy.get(".modal:visible .btn-primary").contains("Sign").click();
}

// Signed: the dialog has closed and the form shows the record as the server now holds it.
function signed() {
	cy.get(".modal.show").should("not.exist");
	settle();
}

function signatures_show(meaning) {
	cy.get(`${FORM} .mds-signatures`).should("contain", meaning);
}

function no_signature() {
	cy.window().should(
		(win) => expect(current(win).__onload?.signatures, "signatures").to.be.undefined
	);
	cy.get(`${FORM} .mds-signatures`).should("not.exist");
}

// The record as stored, read as the signed-in user.
function stored(doctype, name) {
	return cy.call("frappe.client.get", { doctype, name }).then((r) => r.message);
}

// A call typed in the browser's console, as the sheet asks: Frappe shows its refusal on screen.
function console_call(method, args) {
	cy.window().then((win) => {
		win.frappe.call({ method, args });
	});
}

function wrong_password() {
	return `${password}-wrong`;
}

// --- Reading the audit trail and what a record keeps ----------------------------------------

function today_text(win) {
	return win.frappe.datetime.str_to_user(win.frappe.datetime.get_today());
}

// The form's button group `group` (View, say), and its item `label`.
function from_menu(group, label) {
	const menu = `${FORM} .inner-group-button[data-label="${encodeURIComponent(group)}"]`;
	cy.get(`${menu} > button`).click();
	cy.get(`${menu} .dropdown-item[data-label="${encodeURIComponent(label)}"]`).click();
}

function open_dialog() {
	cy.get(".modal.show").should("have.css", "opacity", "1");
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the dialog has finished opening").to.equal(true);
		expect(win.cur_dialog.$wrapper.is(".modal.show"), "it is the dialog shown").to.equal(true);
	});
	return cy.get(".modal.show").last();
}

// The electronic signatures shown on the form (esign.js), first signed first.
function signature_rows() {
	return cy.get(`${FORM} .mds-signatures`).last().find("tbody tr");
}

// A server call the signed-in user makes that must be refused, as a script or another tool would
// send it past the form; returns the refusal.
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

// What a record holds that a merge, a deletion or a cancellation would change, as stored.
function as_stored(doctype, name, fields) {
	return stored(doctype, name).then((doc) =>
		Object.fromEntries(fields.map((field) => [field, doc[field]]))
	);
}

const ITEM_KEPT = [
	"item_name",
	"disabled",
	"mds_approval_status",
	"mds_version",
	"default_bom",
	"quality_inspection_template",
	"modified",
];
const BOM_KEPT = ["item", "docstatus", "is_active", "is_default", "mds_version", "modified"];
const SPECIFICATION_KEPT = ["mds_item", "mds_master_approval", "mds_version", "modified"];

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

// --- The sheet ------------------------------------------------------------------------------

context("D3 Item / BOM / Routing Master, the browser test sheet", { retries: 0 }, () => {
	const failed_rows = new Set();
	const row_id = (title) => title.match(/D3-T\d\d/)?.[0];

	// The row builds on the records of `rows`: when one of them failed or was not run, this row is
	// not run either, and the rows after it that build on it are not run in turn.
	function needs(test, ...rows) {
		if (rows.some((row) => failed_rows.has(row))) {
			failed_rows.add(row_id(test.test.title));
			test.skip();
		}
	}

	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		cy.then(() => set_up("prepare_sheet")).then((message) => {
			d = message;
		});
	});

	beforeEach(function () {
		run.problems.length = 0;
		run.expected.length = 0;
		watch_requests();
	});

	afterEach(function () {
		if (this.currentTest.state === "failed") failed_rows.add(row_id(this.currentTest.title));
	});

	it("[URS-MST-003] [URS-MST-004] D3-T01 QA creates a raw material item; it is a draft and shows what is missing", () => {
		sign_in(QA);
		new_form("Item");
		set({
			item_code: d.item,
			item_group: "Raw Material",
			mds_item_type: "Raw Material",
			stock_uom: "Nos",
		});
		pick("gst_hsn_code", "39269099");
		save();
		open_form("Item", d.item);
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.mds_approval_status, "QA Approval").to.equal("Draft");
			expect(doc.mds_version, "Version").to.equal(1);
			expect(doc.mds_effective_from, "Version Effective From").to.equal(
				win.frappe.datetime.get_today()
			);
		});
		banner_says(
			`${ITEM_DRAFT} Still needed: lot tracking (Has Batch No); a shelf life with an expiry date on every lot; its storage condition; incoming inspection (Inspection Required before Purchase); its incoming specification (module 6); its material or product specification.`
		);
		offered("Approve Item", false);
		no_problems("D3-T01");
	});

	it("[URS-MST-003] D3-T02 A draft item is refused on a material request", () => {
		sign_in(PURCHASE);
		new_form("Material Request");
		cy.window().then((win) =>
			set({ material_request_type: "Purchase", schedule_date: in_days(win, 7) })
		);
		cy.window().then((win) =>
			add_row("items", [
				["item_code", d.draft_item],
				["qty", 100],
				["schedule_date", in_days(win, 7)],
				["warehouse", d.stores.quarantine],
			])
		);
		save_refused(
			`Not yet approved by QA, so not used in Material Request: ${d.draft_item}. QA approves an item on its form.`,
			"Item not approved"
		);
		no_problems("D3-T02");
	});

	it("[URS-MST-003] D3-T03 A draft item is refused as a BOM component", () => {
		sign_in(PRODUCTION);
		new_form("BOM");
		set({ item: d.product, quantity: 100, is_default: 0 });
		add_row("items", [
			["item_code", d.materials.silicone],
			["qty", 1.2],
		]);
		add_row("items", [
			["item_code", d.draft_item],
			["qty", 100],
		]);
		save_refused(
			`Not yet approved by QA, so not used in BOM: ${d.draft_item}. QA approves an item on its form.`,
			"Item not approved"
		);
		no_problems("D3-T03");
	});

	it("[URS-MST-002] [URS-MST-009] D3-T04 An incomplete or duplicate specification is refused", function () {
		needs(this, "D3-T01");
		sign_in(QA);
		new_form(SPECIFICATION);
		set({
			quality_inspection_template_name: d.specification,
			mds_item: d.item,
			mds_stage: "Incoming",
			mds_sampling: FIXED,
		});
		add_row(PARAMETERS, [
			["specification", "Appearance"],
			["numeric", 0],
			["value", "Conforms"],
		]);
		save_refused(
			`Specification ${d.specification} does not yet state the sample size, the method of Appearance.`,
			"Specification incomplete"
		);

		// Step 3: the sample size and the method stated; sampled under ISO 2859-1 without its level.
		set({ mds_sample_size: 5 });
		set_row(PARAMETERS, 1, { mds_method: "Visual" });
		set({ mds_sampling: "ISO 2859-1" });
		save_refused(
			`Specification ${d.specification} does not yet state the inspection level, the AQL.`,
			"Specification incomplete"
		);

		// Step 4: back to a fixed sample size.
		set({ mds_sampling: FIXED });
		save();
		form_is(SPECIFICATION, (doc) => doc.name === d.specification);

		// Step 5: a second specification of the same item and stage.
		new_form(SPECIFICATION);
		set({
			quality_inspection_template_name: d.second_specification,
			mds_item: d.item,
			mds_stage: "Incoming",
			mds_sampling: FIXED,
			mds_sample_size: 5,
		});
		add_row(PARAMETERS, [
			["specification", "Appearance"],
			["mds_method", "Visual"],
			["numeric", 0],
			["value", "Conforms"],
		]);
		save_refused(
			`${d.specification} already holds the incoming specification of ${d.item}; change that one instead.`,
			"One specification per item and stage"
		);
		no_problems("D3-T04");
	});

	it("[URS-MST-003] [URS-MST-004] [URS-MST-006] [URS-MST-016] D3-T05 QA completes the item and approves it with a signature", function () {
		needs(this, "D3-T04");
		sign_in(QA);
		open_form("Item", d.item);
		set({
			has_batch_no: 1,
			has_expiry_date: 1,
			shelf_life_in_days: 365,
			mds_storage_condition: "Below 30 °C, dry",
			inspection_required_before_purchase: 1,
			mds_specification_document: d.specification_document,
		});
		save();

		// The sheet's precondition (its D3-T21, later in its order): QA names the specification's
		// test method and approves it with a signature.
		open_form(SPECIFICATION, d.specification);
		set({ mds_test_method: d.test_method });
		save();
		open_form(SPECIFICATION, d.specification);
		banner_says(MASTER_READY);
		// Not signed over changes not yet saved: a signature binds the record as saved (21 CFR 11.50).
		cy.window().then((win) =>
			win.cur_frm.set_value("mds_sample_size", (win.cur_frm.doc.mds_sample_size || 0) + 1)
		);
		cy.window().its("cur_frm").invoke("is_dirty").should("eq", true);
		button("Approve").click();
		cy.get(".modal.show").should("contain", "Save your changes before you sign");
		cy.window()
			.its("cur_dialog.title")
			.should("not.match", /^Sign: /);
		cy.get(".modal.show .btn-modal-close").first().click();
		cy.get(".modal.show").should("not.exist");
		// The change dropped, as a person reloads the record.
		cy.window().then((win) => win.cur_frm.reload_doc());
		cy.window().its("cur_frm").invoke("is_dirty").should("eq", false);
		sign("Approve", "Parameters, limits, sampling and test method checked");
		signed();
		cy.window().should((win) =>
			expect(current(win).mds_master_approval, "the specification").to.equal("Approved")
		);

		// Step 2: reloaded, the item is ready for QA's approval.
		open_form("Item", d.item);
		banner_says(ITEM_READY);
		offered("Approve Item", true);

		// Step 3: a wrong password.
		expect_refusal(/Incorrect password/);
		sign("Approve Item", "Item data and specification checked", wrong_password());
		refused(WRONG_PASSWORD);
		stored("Item", d.item).then((item) => expect(item.mds_approval_status).to.equal("Draft"));

		// Step 4: the correct password.
		open_form("Item", d.item);
		sign("Approve Item", "Item data and specification checked");
		signed();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.mds_approval_status, "QA Approval").to.equal("Approved");
			expect(doc.mds_approved_by, "Approved By").to.equal(users[QA]);
			expect(doc.mds_approved_at, "Approved At").to.be.ok;
		});
		cy.window().then((win) =>
			banner_says(`Approved by QA (${win.frappe.session.user_fullname}, `)
		);
		banner_says("; its controlled fields change under change control.");
		signatures_show("Approved");
		offered("Approve Item", false);
		no_problems("D3-T05");
	});

	it("[URS-MST-001] [URS-MST-003] [URS-MST-006] D3-T06 An approved item is locked: change control, no lost requirements, no new type", function () {
		needs(this, "D3-T05");
		sign_in(QA);
		open_form("Item", d.item);
		set({ shelf_life_in_days: 730 });
		save_refused(
			`Item ${d.item} is in use: name the approved change request that lists it to change it.`,
			"Change control"
		);

		open_form("Item", d.item);
		set({ mds_storage_condition: "" });
		save_refused(
			`${d.item} is approved and keeps what its approval needed; it cannot lose its storage condition.`,
			"Approved item"
		);

		open_form("Item", d.item);
		set({ mds_item_type: "Packaging" });
		save_refused(
			"The type of an approved item does not change; set up a new item of type Packaging.",
			"Approved item"
		);

		// Step 4: only the description, typed in its editor.
		open_form("Item", d.item);
		reveal("description");
		// The editor's own editable area (the control also holds a read-only copy of the text).
		cy.get(
			`${FORM} .frappe-control[data-fieldname="description"] .ql-editor[contenteditable="true"]:visible`
		)
			.first()
			.click()
			.type(" Described for the browser test.");
		cy.window().should((win) =>
			expect(current(win).description).to.contain("Described for the browser test.")
		);
		save();
		cy.window().should((win) => expect(current(win).mds_version, "Version").to.equal(1));

		// Step 5: under an approved change request that lists the item.
		approved_change("Item", d.item).then((change) => {
			made.item_change = change;
		});
		sign_in(QA);
		open_form("Item", d.item);
		cy.then(() => set({ shelf_life_in_days: 730, mds_change_request: made.item_change }));
		save();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.shelf_life_in_days).to.equal(730);
			expect(doc.mds_version, "Version").to.equal(2);
			expect(doc.mds_effective_from, "Version Effective From").to.equal(
				win.frappe.datetime.get_today()
			);
			expect(doc.mds_changed_under, "Last Changed Under").to.equal(made.item_change);
			expect(doc.mds_change_request || null, "named afresh for the next change").to.equal(
				null
			);
			expect(doc.mds_approval_status, "QA Approval").to.equal("Approved");
		});
		no_problems("D3-T06");
	});

	it("[URS-MST-005] D3-T07 A finished good needs a valid, unique GTIN and a sound lifetime", () => {
		sign_in(QA);
		new_form("Item");
		set({
			item_code: d.finished_good,
			item_group: "Products",
			mds_item_type: "Finished Good",
			stock_uom: "Nos",
			has_batch_no: 1,
			has_expiry_date: 1,
			shelf_life_in_days: 730,
			mds_storage_condition: "Below 30 °C",
		});
		pick("gst_hsn_code", "90183910");
		set({ mds_udi_di: d.invalid_gtin });
		save_refused(
			`${d.invalid_gtin} is not a GTIN: 8, 12, 13 or 14 digits ending in the GS1 check digit.`
		);

		set({ mds_udi_di: d.gtin });
		save();
		open_form("Item", d.finished_good);
		banner_says(
			`${ITEM_DRAFT} Still needed: whether it is supplied sterile; its device class; its pack sizes (units per box, boxes per carton); its final specification (module 6); its material or product specification.`
		);

		set({ mds_device_lifetime_years: 9 });
		save_refused(
			"The device lifetime is 1 to 8 years: its records are kept 10 years, at least the lifetime plus two."
		);
		set({ mds_device_lifetime_years: 1 });
		save_refused(
			`The device lifetime of ${d.finished_good} is 1 years, shorter than its shelf life of 2 years: a device is used until its expiry date.`
		);
		set({ mds_device_lifetime_years: 5 });
		save();
		stored("Item", d.finished_good).then((item) =>
			expect(item.mds_device_lifetime_years).to.equal(5)
		);

		new_form("Item");
		set({
			item_code: d.second_finished_good,
			item_group: "Products",
			mds_item_type: "Finished Good",
			stock_uom: "Nos",
		});
		pick("gst_hsn_code", "90183910");
		set({ mds_udi_di: d.gtin });
		save_refused(
			`GTIN ${d.gtin} identifies ${d.finished_good} already; each item carries its own.`
		);
		no_problems("D3-T07");
	});

	it("[URS-MST-007] D3-T08 Production drafts a bill of materials; no Submit is offered", () => {
		sign_in(PRODUCTION);
		new_form("BOM");
		set({ item: d.product, quantity: 100, is_default: 0, with_operations: 0 });
		cy.then(() => {
			for (const [code, qty] of Object.entries(d.components)) {
				add_row("items", [
					["item_code", code],
					["qty", qty],
				]);
			}
		});
		save();
		cy.window().then((win) => {
			made.bom = current(win).name;
		});
		cy.then(() => open_form("BOM", made.bom));
		cy.window().should((win) => expect(current(win).docstatus, "a draft").to.equal(0));
		banner_says(BOM_READY);
		no_submit();
		offered("Approve BOM", false);
		no_problems("D3-T08");
	});

	it("[URS-MST-007] D3-T09 A work order on a draft bill is refused", function () {
		needs(this, "D3-T08");
		sign_in(PRODUCTION);
		new_form("Work Order");
		set({ production_item: d.product });
		// Typed by name: the lookup lists only approved bills.
		cy.then(() => set({ bom_no: made.bom, qty: 100 }));
		// The stores once the form has laid the order out from the bill: the form replaces the order
		// with the server's on each change of the bill or the quantity, and the test site sets no
		// default stores in Manufacturing Settings.
		set({ wip_warehouse: d.stores.work_in_progress, fg_warehouse: d.stores.quarantine });
		cy.then(() =>
			save_refused(
				`A work order is made from an approved, active bill of materials of ${d.product}; ${made.bom} is not one.`
			)
		);
		no_problems("D3-T09");
	});

	it("[URS-MST-007] D3-T10 QA approves the bill of materials with a signature", function () {
		needs(this, "D3-T08");
		sign_in(QA);
		cy.then(() => open_form("BOM", made.bom));
		banner_says(BOM_READY);
		offered("Approve BOM", true);
		no_submit();

		expect_refusal(/Incorrect password/);
		sign("Approve BOM", "Components and quantities checked", wrong_password());
		refused(WRONG_PASSWORD);
		cy.then(() => stored("BOM", made.bom)).then((bom) =>
			expect(bom.docstatus, "a draft").to.equal(0)
		);

		cy.then(() => open_form("BOM", made.bom));
		sign("Approve BOM", "Components and quantities checked");
		signed();
		cy.window().should((win) => expect(current(win).docstatus, "submitted").to.equal(1));
		banner_says(BOM_APPROVED);
		signatures_show("Approved");
		no_problems("D3-T10");
	});

	it("[URS-MST-007] D3-T11 A bill whose product is not a finished good or sub-assembly cannot be approved", () => {
		// Precondition: Production's draft bill of a raw material.
		sign_in(PRODUCTION);
		cy.insert_doc("BOM", {
			item: d.materials.silicone,
			quantity: 1,
			is_active: 1,
			is_default: 0,
			rm_cost_as_per: "Valuation Rate",
			items: [{ item_code: d.materials.latex, qty: 1, rate: 1 }],
		}).then((bom) => {
			made.raw_material_bom = bom.name;
		});

		sign_in(QA);
		cy.then(() => open_form("BOM", made.raw_material_bom));
		banner_says(
			"Awaiting QA's approval. Still needed: a finished good or sub-assembly as its product."
		);
		offered("Approve BOM", false);
		no_submit();
		no_problems("D3-T11");
	});

	it("[URS-MST-001] D3-T12 A new default bill replaces the one in force only under change control", () => {
		// Precondition: Production duplicates the default bill, which stays marked the default.
		sign_in(PRODUCTION);
		open_form("BOM", d.default_bom);
		cy.window().then((win) => win.cur_frm.copy_doc());
		form_is("BOM", (doc) => doc.__islocal && doc.item === d.product);
		settle();
		set({ is_default: 1 });
		save();
		cy.window().then((win) => {
			made.duplicate_bom = current(win).name;
		});

		sign_in(QA);
		cy.then(() => open_form("BOM", made.duplicate_bom));
		banner_says(BOM_READY);
		expect_refusal(/is the default bill of materials of/);
		sign("Approve BOM", "Replaces the bill in force");
		refused(
			`${d.default_bom} is the default bill of materials of ${d.product}: name the approved change request that lists it to replace it.`,
			"Change control"
		);
		cy.then(() => open_form("BOM", made.duplicate_bom));
		cy.window().should((win) => expect(current(win).docstatus, "still a draft").to.equal(0));
		no_signature();
		stored("BOM", d.default_bom).then((bom) => {
			expect(bom.is_default, "the old bill is still the default").to.equal(1);
			expect(bom.docstatus).to.equal(1);
		});
		no_problems("D3-T12");
	});

	it("[URS-MST-001] D3-T13 ERPNext's BOM Update Tool cannot replace a bill", () => {
		sign_in(MANAGEMENT);
		leave_page();
		cy.visit("/desk/bom-update-tool");
		cy.window().should((win) => expect(win.cur_frm?.doctype).to.equal("BOM Update Tool"));
		settle();
		cy.then(() => set({ current_bom: d.default_bom, new_bom: d.second_bom }));
		expect_refusal(/is not replaced inside approved ones/);
		cy.get(`${FORM} .frappe-control[data-fieldname="replace"] button`)
			.should("not.be.disabled")
			.click();
		refused(REPLACE_REFUSED);

		// Updating the costs stays allowed: ERPNext queues it, or says one is queued already.
		const in_progress = /BOM Updation already in progress/;
		expect_refusal(in_progress);
		cy.get(
			`${FORM} .frappe-control[data-fieldname="update_latest_price_in_all_boms"] button`
		).click();
		cy.window()
			.should((win) => {
				const shown = message_dialog(win);
				expect(shown, "ERPNext's answer").to.not.equal(null);
				expect(shown.text).to.not.contain("not replaced");
				expect(shown.title).to.be.oneOf(["BOM Update Initiated", "Note"]);
			})
			.then((win) => close_message(win));
		cy.then(() => {
			// Queued: no refusal came; the one allowed for is not owed.
			const waiting = run.expected.indexOf(in_progress);
			if (waiting >= 0) run.expected.splice(waiting, 1);
		});
		no_problems("D3-T13");
	});

	it("[URS-MST-012] D3-T14 Production records a workstation and QA approves it", () => {
		sign_in(PRODUCTION);
		cy.insert_doc("Workstation", { workstation_name: d.bench, production_capacity: 1 });

		sign_in(QA);
		open_form("Workstation", d.bench);
		banner_says(MASTER_READY);
		offered("Approve", true);
		sign("Approve", "Bench checked; no equipment is placed at it");
		signed();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.mds_master_approval, "Approval by QA").to.equal("Approved");
			expect(doc.mds_master_approved_by, "Approved By").to.equal(users[QA]);
			expect(doc.mds_master_approved_at, "Approved At").to.be.ok;
		});
		banner_says(MASTER_APPROVED);
		no_problems("D3-T14");
	});

	it("[URS-MST-012] D3-T15 An operation is approved only with an effective work instruction and a stated in-process check", function () {
		needs(this, "D3-T14");
		sign_in(PRODUCTION);
		new_form("Operation");
		cy.get(`${FORM} .frappe-control[data-fieldname="__newname"] input:visible`)
			.click()
			.type(d.operation)
			.blur();
		cy.window().should((win) => expect(current(win).__newname).to.equal(d.operation));
		// The lookup offers work instructions only; the SOP is entered by its name.
		set({ workstation: d.bench, mds_work_instruction: d.test_method });
		save();
		open_form("Operation", d.operation);
		banner_says(
			`${MASTER_DRAFT} Still needed: a work instruction as its document (${d.test_method} is a SOP, not of type Work Instruction); whether it carries an in-process check.`
		);

		set({ mds_work_instruction: d.work_instruction, mds_in_process_check: "Yes" });
		save();
		open_form("Operation", d.operation);
		banner_says(MASTER_READY);
		offered("Approve", false);
		no_problems("D3-T15");
	});

	it("[URS-MST-012] D3-T16 QA approves the operation, then a routing with it", function () {
		needs(this, "D3-T15");
		sign_in(QA);
		open_form("Operation", d.operation);
		banner_says(MASTER_READY);
		sign("Approve", "Work instruction and in-process check in place");
		signed();
		cy.window().should((win) =>
			expect(current(win).mds_master_approval, "Approval by QA").to.equal("Approved")
		);
		banner_says(MASTER_APPROVED);

		// Precondition: Production's routing with the operation at the bench.
		sign_in(PRODUCTION);
		cy.insert_doc("Routing", {
			routing_name: d.routing,
			operations: [
				{ operation: d.operation, workstation: d.bench, time_in_mins: 10, sequence_id: 1 },
			],
		});

		sign_in(QA);
		open_form("Routing", d.routing);
		banner_says(MASTER_READY);
		sign("Approve", "Operations and workstations approved");
		signed();
		cy.window().should((win) =>
			expect(current(win).mds_master_approval, "Approval by QA").to.equal("Approved")
		);
		banner_says(MASTER_APPROVED);
		no_problems("D3-T16");
	});

	it("[URS-MST-012] D3-T17 A routing with a draft operation cannot be approved, and a bill on it is refused", () => {
		// Precondition: Production's draft operation, a routing with it, and a bill on that routing.
		sign_in(PRODUCTION);
		cy.insert_doc("Operation", {
			name: d.second_operation,
			workstation: d.approved_bench,
			mds_work_instruction: d.work_instruction,
			mds_in_process_check: "No",
		});
		cy.insert_doc("Routing", {
			routing_name: d.second_routing,
			operations: [
				{
					operation: d.second_operation,
					workstation: d.approved_bench,
					time_in_mins: 10,
					sequence_id: 1,
				},
			],
		});
		cy.then(() =>
			cy.insert_doc("BOM", {
				item: d.product,
				quantity: 100,
				is_active: 1,
				is_default: 0,
				with_operations: 1,
				routing: d.second_routing,
				rm_cost_as_per: "Valuation Rate",
				items: Object.entries(d.components).map(([item_code, qty]) => ({
					item_code,
					qty,
					rate: 1,
				})),
			})
		).then((bom) => {
			made.routed_bom = bom.name;
		});

		sign_in(QA);
		open_form("Routing", d.second_routing);
		banner_says(`${MASTER_DRAFT} Still needed: operation ${d.second_operation} approved.`);
		offered("Approve", false);

		cy.then(() => open_form("BOM", made.routed_bom));
		banner_says(BOM_READY);
		expect_refusal(/is made only on what QA has approved and is in use now/);
		sign("Approve BOM", "Components, quantities and routing checked");
		refused(
			`A bill of materials is made only on what QA has approved and is in use now: operation ${d.second_operation} not approved; routing ${d.second_routing} not approved.`,
			"Not approved by QA"
		);
		cy.then(() => open_form("BOM", made.routed_bom));
		cy.window().should((win) => expect(current(win).docstatus, "still a draft").to.equal(0));
		no_signature();
		no_problems("D3-T17");
	});

	it("[URS-MST-001] [URS-MST-012] D3-T18 An approved operation changes only under an approved change request", function () {
		needs(this, "D3-T16");
		sign_in(PRODUCTION);
		open_form("Operation", d.operation);
		cy.window().then((win) => set({ batch_size: (current(win).batch_size || 0) + 1 }));
		save_refused(
			`Operation ${d.operation} is in use: name the approved change request that lists it to change it.`,
			"Change control"
		);

		approved_change("Operation", d.operation).then((change) => {
			made.operation_change = change;
		});
		sign_in(PRODUCTION);
		open_form("Operation", d.operation);
		cy.window().then((win) =>
			set({
				batch_size: (current(win).batch_size || 0) + 2,
				mds_change_request: made.operation_change,
			})
		);
		save();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.mds_version, "Version").to.equal(2);
			expect(doc.mds_changed_under, "Last Changed Under").to.equal(made.operation_change);
			expect(doc.mds_master_approval, "Approval by QA").to.equal("Approved");
		});
		no_problems("D3-T18");
	});

	it("[URS-MST-002] [URS-MST-014] D3-T19 A specification in use changes only under an approved change request, and goes back to Draft", () => {
		const raised = (value) => Math.round((Number(value) + 0.1) * 1000) / 1000;
		sign_in(QA);
		open_form(SPECIFICATION, d.silicone_specification);
		cy.window().then((win) => {
			const row = row_of(win, PARAMETERS, "specification", DIMENSION);
			set_row(PARAMETERS, row.idx, { max_value: raised(row.max_value) });
		});
		save_refused(
			`Specification ${d.silicone_specification} is in use: name the approved change request that lists it to change it.`,
			"Change control"
		);

		open_form(SPECIFICATION, d.silicone_specification);
		set({ mds_item: d.product });
		save_refused(
			`Specification ${d.silicone_specification} is the incoming specification of approved item ${d.materials.silicone}; it stays theirs.`,
			"Change control"
		);

		approved_change(SPECIFICATION, d.silicone_specification).then((change) => {
			made.specification_change = change;
		});
		sign_in(QA);
		open_form(SPECIFICATION, d.silicone_specification);
		cy.window().then((win) => {
			made.specification_version = current(win).mds_version;
			const row = row_of(win, PARAMETERS, "specification", DIMENSION);
			set_row(PARAMETERS, row.idx, { max_value: raised(row.max_value) });
			set({ mds_change_request: made.specification_change });
		});
		save();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.mds_version, "Version").to.equal(made.specification_version + 1);
			expect(doc.mds_changed_under, "Last Changed Under").to.equal(
				made.specification_change
			);
			expect(doc.mds_change_request || null, "Change Request for This Change").to.equal(
				null
			);
			expect(doc.mds_master_approval, "Approval by QA").to.equal("Draft");
			expect(doc.mds_master_approved_by || null, "Approved By").to.equal(null);
		});

		open_form(SPECIFICATION, d.silicone_specification);
		sign("Approve", "Limit change checked");
		signed();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.mds_master_approval, "Approval by QA").to.equal("Approved");
			expect(doc.mds_master_approved_by, "Approved By").to.equal(users[QA]);
		});
		banner_says(SPECIFICATION_APPROVED);
		no_problems("D3-T19");
	});

	it("[URS-MST-003] [URS-MST-007] [URS-MST-012] D3-T20 Other jobs cannot approve masters", function () {
		needs(this, "D3-T07", "D3-T17");
		sign_in(PRODUCTION);
		open_form("Item", d.finished_good);
		banner_says(ITEM_DRAFT);
		cy.window().should(
			(win) => expect(win.cur_frm.perm[0]?.write, "Production may write").to.not.be.ok
		);
		offered("Approve Item", false);

		cy.then(() => open_form("BOM", made.routed_bom));
		banner_says(BOM_READY);
		no_submit();
		offered("Approve BOM", false);

		open_form("Operation", d.second_operation);
		banner_says(MASTER_READY);
		offered("Approve", false);
		no_problems("D3-T20");
	});

	it("[URS-MST-014] [URS-MST-015] D3-T21 QA approves a specification with a signature once its test method is in effect", () => {
		sign_in(QA);
		open_form(SPECIFICATION, d.specification_b);
		banner_says(`${MASTER_DRAFT} Still needed: its test method.`);
		offered("Approve", false);

		// Step 2: the lookup offers only SOPs, work instructions and external documents; the server
		// is checked from the console with a form in effect.
		cy.window().then((win) => {
			const query = win.cur_frm.fields_dict.mds_test_method.get_query();
			expect(query.filters.document_type).to.deep.equal(["in", TEST_METHOD_TYPES]);
		});
		cy.window().then((win) =>
			win.frappe.xcall("frappe.client.set_value", {
				doctype: SPECIFICATION,
				name: d.specification_b,
				fieldname: "mds_test_method",
				value: d.form_document,
			})
		);
		open_form(SPECIFICATION, d.specification_b);
		banner_says(
			`a test method as its document (${d.form_document} is a Form, not of type SOP / Work Instruction / External Document)`
		);

		// Step 3: the SOP in effect.
		set({ mds_test_method: d.test_method });
		save();
		open_form(SPECIFICATION, d.specification_b);
		banner_says(MASTER_READY);
		offered("Approve", true);

		// Step 4: no reason; the dialog asks for it, and so does the server.
		sign("Approve", "");
		cy.window().should((win) => {
			const shown = message_dialog(win);
			expect(shown, "the dialog's message").to.not.equal(null);
			expect(shown.title).to.equal("Missing Values Required");
			expect(shown.text).to.contain("Reason");
		});
		cy.window().then((win) => close_message(win));
		expect_refusal(/Say why the quality inspection template is approved/);
		console_call("meddevice_suite.compliance.signing.sign_action", {
			doctype: SPECIFICATION,
			name: d.specification_b,
			action: "Approve",
			password,
			meaning: "Approved",
		});
		refused("Say why the quality inspection template is approved.");

		// Step 5: a wrong password.
		open_form(SPECIFICATION, d.specification_b);
		expect_refusal(/Incorrect password/);
		sign("Approve", "Parameters, limits, sampling and test method checked", wrong_password());
		refused(WRONG_PASSWORD);
		stored(SPECIFICATION, d.specification_b).then((doc) =>
			expect(doc.mds_master_approval).to.equal("Draft")
		);

		// Step 6: the correct password.
		open_form(SPECIFICATION, d.specification_b);
		sign("Approve", "Parameters, limits, sampling and test method checked");
		signed();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.mds_master_approval, "Approval by QA").to.equal("Approved");
			expect(doc.mds_master_approved_by, "Approved By").to.equal(users[QA]);
			expect(doc.mds_master_approved_at, "Approved At").to.be.ok;
		});
		banner_says(SPECIFICATION_APPROVED);
		signatures_show("Approved");

		// Step 7: a limit changed before it is in use: saved, and in draft again. Opened again first:
		// the form reloads itself a moment after the signature, which would drop a change typed
		// meanwhile.
		open_form(SPECIFICATION, d.specification_b);
		cy.window().then((win) => {
			const row = row_of(win, PARAMETERS, "specification", DIMENSION);
			set_row(PARAMETERS, row.idx, { max_value: Number(row.max_value) + 0.5 });
		});
		save();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.mds_master_approval, "Approval by QA").to.equal("Draft");
			expect(doc.mds_master_approved_by || null, "Approved By").to.equal(null);
		});
		// Approved again, as the sheet asks before the item is approved.
		open_form(SPECIFICATION, d.specification_b);
		sign("Approve", "Changed limit checked");
		signed();
		cy.window().should((win) =>
			expect(current(win).mds_master_approval, "Approval by QA").to.equal("Approved")
		);
		no_problems("D3-T21");
	});

	it("[URS-MST-014] D3-T22 No inspection against a specification in draft; QC cannot approve one", () => {
		// Precondition: the specification changed under a change request (D3-T19 steps 1 to 3), so
		// it is in draft again; the receipt of the material is submitted and its certificate verified.
		set_up("return_specification_to_draft", { name: d.silicone_specification }).then(
			(result) => {
				made.silicone_max = result.max_value;
			}
		);

		sign_in(QC);
		open_form(SPECIFICATION, d.silicone_specification);
		banner_says(MASTER_DRAFT);
		offered("Approve", false);

		expect_refusal(/Only QA approves a quality inspection template/);
		console_call("meddevice_suite.compliance.signing.sign_action", {
			doctype: SPECIFICATION,
			name: d.silicone_specification,
			action: "Approve",
			password,
			reason: "x",
			meaning: "Approved",
		});
		refused("Only QA approves a quality inspection template.");

		const inspect = () => {
			new_form("Quality Inspection");
			set({ inspection_type: "Incoming", reference_type: "Purchase Receipt" });
			set({ reference_name: d.receipt });
			set({ item_code: d.materials.silicone });
			set({ batch_no: d.silicone_lot, sample_size: 0 });
		};
		inspect();
		save_refused(
			`Specification ${d.silicone_specification} of ${d.materials.silicone} is in draft: new, or changed since QA approved it. QA approves it with a signature before ${d.materials.silicone} is inspected against it.`,
			"Specification not approved"
		);

		sign_in(QA);
		open_form(SPECIFICATION, d.silicone_specification);
		sign("Approve", "Limit change checked");
		signed();
		cy.window().should((win) =>
			expect(current(win).mds_master_approval, "Approval by QA").to.equal("Approved")
		);

		sign_in(QC);
		inspect();
		cy.window().should((win) => {
			const readings = current(win).readings || [];
			expect(
				readings.map((row) => row.specification),
				"laid out from the specification"
			).to.deep.equal(["Appearance", DIMENSION]);
			expect(readings[1].max_value, "its limit").to.be.closeTo(made.silicone_max, 1e-6);
		});
		set_row("readings", 1, { reading_value: "Conforms" });
		set_row("readings", 2, { reading_1: "10.00", mds_instrument: d.instrument });
		save();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.quality_inspection_template, "against").to.equal(d.silicone_specification);
			expect(doc.sample_size, "the specification's sample size").to.equal(5);
		});
		no_problems("D3-T22");
	});

	it("[URS-MST-016] D3-T23 A raw material, packaging item or finished good is approved only with its specification document in effect", function () {
		needs(this, "D3-T21");
		sign_in(QA);
		open_form("Item", d.item_b);
		banner_says(`${ITEM_DRAFT} Still needed: its material or product specification.`);
		offered("Approve Item", false);

		// Step 2: the lookup offers Specification documents only; the server is checked from the
		// console with an SOP in effect.
		cy.window().then((win) => {
			const query = win.cur_frm.fields_dict.mds_specification_document.get_query();
			expect(query.filters.document_type).to.deep.equal(["in", ["Specification"]]);
		});
		cy.window().then((win) =>
			win.frappe.xcall("frappe.client.set_value", {
				doctype: "Item",
				name: d.item_b,
				fieldname: "mds_specification_document",
				value: d.test_method,
			})
		);
		open_form("Item", d.item_b);
		banner_says(
			`a material or product specification as its document (${d.test_method} is a SOP, not of type Specification)`
		);

		// Step 3: a Specification still in draft.
		set({ mds_specification_document: d.draft_specification_document });
		save();
		open_form("Item", d.item_b);
		banner_says(
			`its material or product specification in effect (${d.draft_specification_document} is draft)`
		);

		// Step 4: a Specification in effect.
		set({ mds_specification_document: d.specification_document });
		save();
		open_form("Item", d.item_b);
		banner_says(ITEM_READY);
		offered("Approve Item", true);

		// Step 5: a sub-assembly is not defined by a document of its own.
		open_form("Item", d.sub_assembly);
		banner_says(ITEM_READY);
		cy.get(`${FORM} .form-message`).should("not.contain", "specification");
		no_problems("D3-T23");
	});

	it("[URS-MST-006] [URS-MST-014] D3-T24 An item is not approved while its specification is in draft", () => {
		sign_in(QA);
		open_form("Item", d.item_rm2);
		banner_says(
			`${ITEM_DRAFT} Still needed: its incoming specification ${d.specification_rm2} approved by QA.`
		);
		offered("Approve Item", false);

		expect_refusal(/cannot be approved yet; it lacks/);
		console_call("meddevice_suite.compliance.signing.sign_action", {
			doctype: "Item",
			name: d.item_rm2,
			action: "Approve",
			password,
			reason: "checked",
			meaning: "Approved",
		});
		refused(
			`${d.item_rm2} cannot be approved yet; it lacks its incoming specification ${d.specification_rm2} approved by QA.`
		);
		open_form("Item", d.item_rm2);
		cy.window().should((win) => expect(current(win).mds_approval_status).to.equal("Draft"));
		no_signature();
		no_problems("D3-T24");
	});

	it("[URS-QMS-006] [URS-MST-015] D3-T25 A test method is withdrawn only once no approved specification names it", () => {
		// The sheet's step 2 withdrew the method while the approved specification still named it, and
		// step 3 then refused the inspection. Since the independent review of 2026-10-15 the
		// withdrawal itself is refused while an approved master names the document as the one
		// defining it (document_control.withdraw, defining_documents.masters_defined_by), so the
		// inspection refusal for a method no longer in effect is no longer reached from the desk.
		const withdraw = () => {
			sign_in(QA);
			open_form("Controlled Document", d.t25.test_method);
			settle();
			cy.get(`${FORM} .inner-group-button[data-label="Actions"] button`).click();
			// The menu's items render in the open dropdown, outside the button group.
			cy.get(".dropdown-menu.show")
				.contains(".dropdown-item", /^\s*Withdraw\s*$/)
				.click();
			cy.get(".modal.show").should("have.css", "opacity", "1");
			cy.window().should((win) => expect(win.cur_dialog?.title).to.equal("Sign: Withdraw"));
			cy.get('.modal.show textarea[data-fieldname="reason"]')
				.click()
				.type("Replaced (test)");
			cy.window()
				.its("cur_dialog")
				.should((dialog) =>
					expect(dialog.get_value("reason")).to.equal("Replaced (test)")
				);
			cy.get('.modal.show input[data-fieldname="password"]')
				.click()
				.type(password, { log: false });
			cy.window()
				.its("cur_dialog")
				.should((dialog) => expect(dialog.get_value("password")).to.equal(password));
			cy.get(".modal:visible .btn-primary").contains("Sign").click();
		};

		// Step 2: QA withdraws the SOP made only for this run; refused, naming the specification.
		const refusal = `${d.t25.test_method} defines ${SPECIFICATION} ${d.t25.specification}, approved and in use. Point each to another document, or retire it, under change control before withdrawing ${d.t25.test_method}.`;
		expect_refusal(new RegExp(escaped(refusal.slice(0, 60))));
		withdraw();
		refused(refusal);
		// The signing dialog stays open on a refusal; QA closes it.
		cy.window().then((win) =>
			until(() => {
				if (!win.document.querySelector(".modal.show")) return true;
				if (win.cur_dialog?.display) win.cur_dialog.hide();
				return false;
			}, 10000).then((closed) => expect(closed, "the signing dialog closes").to.be.true)
		);
		settle();
		stored("Controlled Document", d.t25.test_method).then((doc) =>
			expect(doc.status, "the method stays in effect").to.equal("Effective")
		);

		// Step 3: QA points the specification to the method in effect, under a change request as it
		// is in use; the change returns it to draft, and QA approves it again.
		sign_in(QA);
		open_form(SPECIFICATION, d.t25.specification);
		cy.window().should((win) =>
			expect(current(win).mds_master_approval, "Approval by QA").to.equal("Approved")
		);
		banner_says(SPECIFICATION_APPROVED);
		approved_change(SPECIFICATION, d.t25.specification).then((change) => {
			made.test_method_change = change;
		});
		sign_in(QA);
		open_form(SPECIFICATION, d.t25.specification);
		cy.then(() =>
			set({ mds_test_method: d.test_method, mds_change_request: made.test_method_change })
		);
		save();
		cy.window().should((win) =>
			expect(current(win).mds_master_approval, "Approval by QA").to.equal("Draft")
		);
		open_form(SPECIFICATION, d.t25.specification);
		sign("Approve", "Test method in effect named");
		signed();
		cy.window().should((win) =>
			expect(current(win).mds_master_approval, "Approval by QA").to.equal("Approved")
		);

		// Step 4: no approved master names the old SOP now; QA withdraws it.
		withdraw();
		signed();
		cy.window().should((win) => expect(current(win).status).to.equal("Obsolete"));
		no_problems("D3-T25");
	});

	// 21 CFR 11.10(e): QA reads who changed an approved item, when, from what to what and under which
	// change request, and who approved the item and its bill of materials with a signature, when and
	// why, on the forms and in the audit trail. The records are the set-up's own (qa/sheets/d3.py
	// audited_product), so the row runs whatever the rows before it did.
	it("[URS-MST-001] [URS-CMP-012] D3-T26 QA reads the audit trail of an approved item and its bill of materials", () => {
		const item = () => d.audited.item;
		const bom = () => d.audited.bom;
		const change = () => d.audited.change_request;
		const shelf_life = () => d.audited.shelf_life;
		const qa = () => d.full_names[QA];
		const audit = {};
		sign_in(QA);
		cy.visit("/desk"); // the server calls below take their token from a desk page

		// The entry of the record's audit trail for `action` whose changes name `naming`, opened
		// from the audit trail's list.
		const open_event = (doctype, name, action, naming) => {
			cy.call("frappe.client.get_list", {
				doctype: "Audit Event",
				filters: { reference_doctype: doctype, reference_name: name, action },
				fields: ["name", "changes"],
				limit_page_length: 0,
			}).then(({ message }) => {
				const naming_it = message.filter((event) =>
					(typeof event.changes === "string"
						? event.changes
						: JSON.stringify(event.changes)
					).includes(naming)
				);
				expect(naming_it, `one ${action} entry names ${naming}`).to.have.length(1);
				audit.event = naming_it[0].name;
			});
			cy.then(() =>
				cy.get(`.page-container:visible .list-row a[data-name="${audit.event}"]`).click()
			);
			cy.then(() => form_is("Audit Event", (doc) => doc.name === audit.event));
			settle();
		};

		// Who (QA), when, and the old and new values, on the entry's form.
		const event_shows = (doctype, name, action, values) => {
			cy.window().should((win) => {
				const doc = current(win);
				expect([doc.reference_doctype, doc.reference_name]).to.deep.equal([doctype, name]);
				expect(doc.action).to.equal(action);
				expect(doc.user, "who").to.equal(users[QA]);
				expect(doc.event_at.slice(0, 10), "when").to.equal(
					win.frappe.datetime.get_today()
				);
			});
			cy.get(`${FORM} .frappe-control[data-fieldname="action"]`).should("contain", action);
			cy.get(`${FORM} .frappe-control[data-fieldname="user"]`).should(
				($control) =>
					expect(
						[users[QA], qa()].some((who) => $control.text().includes(who)),
						"who, shown"
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
				for (const value of values) {
					expect(text, "the old and new values, as stored").to.contain(value);
				}
			});
		};

		// QA's signature on the form: who signed, when and why.
		const signed_by_qa = (reason) =>
			cy.window().then((win) =>
				signature_rows().should(($rows) => {
					expect($rows).to.have.length(1);
					expect($rows.eq(0).text())
						.to.contain("Approved")
						.and.to.contain(qa())
						.and.to.contain(today_text(win))
						.and.to.contain(reason);
				})
			);

		// On the item's form: who changed its shelf life, when, the old and new value, and the
		// approved change request it was changed under; and QA's signed approval.
		cy.then(() => open_form("Item", item()));
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.mds_approval_status, "QA Approval").to.equal("Approved");
			expect(doc.shelf_life_in_days).to.equal(shelf_life().after);
			expect(doc.mds_version, "Version").to.equal(2);
			expect(doc.mds_changed_under, "Last Changed Under").to.equal(change());
		});
		cy.then(() =>
			audit_entry_shown([
				`Updated by ${qa()}`,
				`Shelf Life In Days: ${shelf_life().before} → ${shelf_life().after}`,
				`Last Changed Under: empty → ${change()}`,
			])
		);
		cy.then(() => signed_by_qa(d.audited.item_approval_reason));
		cy.then(() => audit_entry_shown([`Updated by ${qa()}`, "QA Approval: Draft → Approved"]));

		// Its whole audit trail, from the form's View menu: the entry of the change, then the entry
		// of the approval, opened.
		cy.then(() => open_audit_trail("Item", item(), ["Created", "Updated"]));
		cy.then(() => open_event("Item", item(), "Updated", change()));
		cy.then(() =>
			event_shows("Item", item(), "Updated", [
				"shelf_life_in_days",
				String(shelf_life().before),
				String(shelf_life().after),
				"mds_changed_under",
				change(),
			])
		);
		cy.then(() => open_form("Item", item()));
		cy.then(() => open_audit_trail("Item", item(), ["Updated"]));
		cy.then(() => open_event("Item", item(), "Updated", "mds_approval_status"));
		cy.then(() =>
			event_shows("Item", item(), "Updated", [
				"mds_approval_status",
				"Draft",
				"Approved",
				"mds_approved_by",
				users[QA],
			])
		);

		// Why: the approved change request the item was changed under.
		cy.then(() => open_form("Change Request", change()));
		cy.window().should((win) => {
			expect(current(win).status).to.equal("Approved");
			expect(current(win).reason).to.equal(d.audited.change_reason);
		});
		reveal("reason");
		cy.then(() =>
			cy
				.get(`${FORM} .frappe-control[data-fieldname="reason"]`)
				.should("contain", d.audited.change_reason)
		);

		// The item's bill of materials: QA's Approved signature, and its approval (its submit) in
		// its audit trail.
		cy.then(() => open_form("BOM", bom()));
		cy.window().should((win) => {
			expect(current(win).item).to.equal(item());
			expect(current(win).docstatus, "approved").to.equal(1);
		});
		banner_says(BOM_APPROVED);
		cy.then(() => signed_by_qa(d.audited.bom_approval_reason));
		cy.then(() => audit_entry_shown([`Submitted by ${qa()}`]));
		cy.then(() => open_audit_trail("BOM", bom(), ["Created", "Submitted"]));
		cy.then(() => open_event("BOM", bom(), "Submitted", ""));
		cy.then(() => event_shows("BOM", bom(), "Submitted", []));
		no_problems("D3-T26");
	});

	// ISO 13485 4.2.4, 4.2.5: an item, a bill of materials and a specification are never merged into
	// another, which would move what QA approved to a record QA never approved it for; and they are
	// kept: no role holds delete on them, so no one is offered Delete, not even the Administrator,
	// and a call past the form is refused. Only a bill of materials is cancelled, by QA and only
	// under an approved change request that lists it (master_data/versioning.py guard_cancel), so
	// Cancel is offered on it and refused without one. The records are the set-up's own (qa/sheets/
	// d3.py), so the row runs whatever the rows before it did.
	it("[URS-MST-001] [URS-MST-017] [URS-CMP-016] D3-T27 An item, a bill of materials and a specification are never merged, deleted or cancelled", () => {
		const k = () => d.kept;
		const never_merged = (doctype, old, into) =>
			`${doctype} ${old} is not merged into ${into}: approvals and history stay with the record they were made for. Disable the duplicate instead.`;
		const cancelled_only_under_change = (bom) =>
			`${bom} is in use: name the approved change request that lists it to cancel it.`;
		// Neither an item nor a specification is ever submitted, so neither is cancelled.
		const draft_not_cancelled = /Cannot change docstatus from 0 \(Draft\) to 2 \(Cancelled\)/;
		// Frappe refuses to delete a submitted record before any rule of the bill's is reached.
		const submitted_not_deleted = /Submitted Record cannot be deleted/;
		// What keeps an approved item: the records that name it (its bill, its specification).
		const item_kept = /You can disable this Item instead of deleting it\./;
		const records = () => [
			["Item", k().item, ITEM_KEPT],
			["Item", k().duplicate, ITEM_KEPT],
			["BOM", k().bom, BOM_KEPT],
			["BOM", k().duplicate_bom, BOM_KEPT],
			[SPECIFICATION, k().specification, SPECIFICATION_KEPT],
			[SPECIFICATION, k().duplicate_specification, SPECIFICATION_KEPT],
		];
		const merges = () => [
			["Item", k().item, k().duplicate],
			["BOM", k().bom, k().duplicate_bom],
			[SPECIFICATION, k().specification, k().duplicate_specification],
		];
		const merge_refused = (who) => {
			for (const [doctype, old_name, new_name] of merges()) {
				cy.then(() =>
					refused_call("frappe.client.rename_doc", {
						doctype,
						old_name,
						new_name,
						merge: 1,
					})
				).then(({ message }) =>
					expect(message, `${doctype} merged by a call, as ${who}`).to.contain(
						never_merged(doctype, old_name, new_name)
					)
				);
			}
		};
		const target = (doctype, name) => ({ doctype, name });
		const before = {};

		// QA, who keeps items (Item Manager): Rename with "Merge with existing" into another
		// approved item, on the form.
		sign_in(QA);
		cy.visit("/desk"); // the server calls below take their token from a desk page
		cy.then(() => {
			for (const [doctype, name, fields] of records()) {
				as_stored(doctype, name, fields).then((kept) => {
					before[`${doctype} ${name}`] = kept;
				});
			}
		});
		cy.then(() => open_form("Item", k().item));
		cy.window().should((win) =>
			expect(current(win).mds_approval_status, "QA Approval").to.equal("Approved")
		);
		settle();
		cy.get(`${FORM} .menu-btn-group > button`).click();
		// Frappe draws the menu outside its button group: the visible menu's item, by its text.
		cy.get(".dropdown-menu.show")
			.contains(".dropdown-item", /^\s*Rename\s*$/)
			.click();
		open_dialog().should("contain", "New Name");
		cy.then(() => {
			cy.get('.modal.show input[data-fieldname="new_name"]').clear().type(k().duplicate);
			cy.get('.modal.show input[data-fieldname="merge"]').check();
			cy.window()
				.its("cur_dialog")
				.should((dialog) => {
					expect(dialog.get_value("new_name")).to.equal(k().duplicate);
					expect(dialog.get_value("merge")).to.equal(1);
				});
			expect_refusal(/is not merged into/);
			cy.get(".modal.show .btn-primary").contains("Rename").click();
			refused(never_merged("Item", k().item, k().duplicate));
		});
		// Nor by a call past the form: the item, its bill of materials or its specification.
		merge_refused("QA");

		// The approved item and its specification: neither Delete nor Cancel, and a call past the
		// form is refused.
		for (const [doctype, name] of [
			["Item", () => k().item],
			[SPECIFICATION, () => k().specification],
		]) {
			cy.then(() => open_form(doctype, name()));
			offered("Cancel", false);
			in_menu("Delete", false);
			cy.then(() => refused_call("frappe.client.delete", target(doctype, name()))).then(
				({ status, message }) => {
					expect(status, `deleting the ${doctype} as QA`).to.equal(403);
					expect(message).to.match(/PermissionError/);
				}
			);
			cy.then(() => refused_call("frappe.client.cancel", target(doctype, name()))).then(
				({ message }) => expect(message).to.match(draft_not_cancelled)
			);
		}
		cy.then(() => open_form(SPECIFICATION, k().specification));
		banner_says(SPECIFICATION_APPROVED);

		// The approved bill of materials: no Delete on a submitted record. QA holds cancel, which
		// takes a bill out of force under an approved change request that lists it; without one,
		// QA's Cancel is refused, on the form and past it.
		cy.then(() => open_form("BOM", k().bom));
		banner_says(BOM_APPROVED);
		in_menu("Delete", false);
		offered("Cancel", true);
		button("Cancel").click();
		cy.then(() => open_dialog().should("contain", `Reason for cancelling ${k().bom}`));
		cy.get('.modal.show textarea[data-fieldname="reason"]')
			.click()
			.type("Bill no longer used (browser test)");
		cy.window()
			.its("cur_dialog")
			.should((dialog) =>
				expect(dialog.get_value("reason")).to.equal("Bill no longer used (browser test)")
			);
		cy.get(".modal.show .btn-primary").contains("Continue").click();
		open_dialog().should("contain", "Permanently Cancel");
		expect_refusal(/name the approved change request that lists it to cancel it/);
		cy.get(".modal.show .btn-primary").contains("Yes").click();
		cy.then(() => refused(cancelled_only_under_change(k().bom), "Change control"));
		cy.then(() => stored("BOM", k().bom)).then((doc) =>
			expect(doc.docstatus, "still approved").to.equal(1)
		);
		cy.then(() => refused_call("frappe.client.cancel", target("BOM", k().bom))).then(
			({ message }) => expect(message).to.contain(cancelled_only_under_change(k().bom))
		);
		cy.then(() => refused_call("frappe.client.delete", target("BOM", k().bom))).then(
			({ status, message }) => {
				expect(status, "deleting the bill as QA").to.equal(403);
				expect(message).to.match(/PermissionError/);
			}
		);
		no_problems("D3-T27, QA");

		// The Administrator, who holds every role.
		sign_in_as_administrator();
		merge_refused("the Administrator");
		// No role holds delete on an item: the menu offers it to nobody, and a call past the form is
		// refused while records name the item.
		cy.then(() => open_form("Item", k().item));
		offered("Cancel", false);
		in_menu("Delete", false);
		cy.then(() => refused_call("frappe.client.delete", target("Item", k().item))).then(
			({ message }) => expect(message).to.match(item_kept)
		);
		cy.then(() => refused_call("frappe.client.cancel", target("Item", k().item))).then(
			({ message }) => expect(message).to.match(draft_not_cancelled)
		);
		// The specification in use: refused by its own rule (master_data/specification.py).
		cy.then(() => open_form(SPECIFICATION, k().specification));
		offered("Cancel", false);
		in_menu("Delete", false);
		cy.then(() =>
			refused_call("frappe.client.delete", target(SPECIFICATION, k().specification))
		).then(({ message }) =>
			expect(message).to.contain(
				`Specification ${k().specification} is in use and is never deleted.`
			)
		);
		cy.then(() =>
			refused_call("frappe.client.cancel", target(SPECIFICATION, k().specification))
		).then(({ message }) => expect(message).to.match(draft_not_cancelled));
		// The bill of materials: the Administrator holds QA's role, so Cancel is offered, and refused
		// without an approved change request; a submitted record is never deleted.
		cy.then(() => open_form("BOM", k().bom));
		in_menu("Delete", false);
		offered("Cancel", true);
		cy.then(() => refused_call("frappe.client.cancel", target("BOM", k().bom))).then(
			({ message }) => expect(message).to.contain(cancelled_only_under_change(k().bom))
		);
		cy.then(() => refused_call("frappe.client.delete", target("BOM", k().bom))).then(
			({ message }) => expect(message).to.match(submitted_not_deleted)
		);

		// Every record is still there, as it was.
		cy.then(() => {
			for (const [doctype, name, fields] of records()) {
				as_stored(doctype, name, fields).then((kept) =>
					expect(kept, `${doctype} ${name}, unchanged`).to.deep.equal(
						before[`${doctype} ${name}`]
					)
				);
			}
		});
		no_problems("D3-T27");
	});
});

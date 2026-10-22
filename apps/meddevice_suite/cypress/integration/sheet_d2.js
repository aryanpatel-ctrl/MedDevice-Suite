// Browser test sheet of data store D2, Vendor Master: approved suppliers, QA's signed supplier
// approval and the system's supplier rating (module 4; URS-PUR-001, URS-PUR-002, URS-MST-001;
// meddevice_suite/master_data/supplier_approval.py, supplier_rating.py). One test per line of the
// sheet, in its order, each signed in as the job the sheet names and done on the screens as that
// person would: Purchase creates a supplier, QA decides it with signatures, purchase orders, goods
// receipts and incoming inspections are checked against the decision in force, and a low rating
// is closed by QA's next decision. Refusals are checked word for word.
//
// The sheet names pilot records; the test site has none, so the run uses its own:
//   RM-POUCH -> UIJ-RM-POUCH, RM-CARTON -> UIJ-RM-CONNECTOR (an item the supplier is not approved
//   for), RM-SILICONE -> UIJ-RM-SILICONE (the journey's approved raw materials, ui_journey.py);
//   "TEST-D2 Pouch Supplier" and the suppliers of D2-T13 and D2-T14 carry the run's tag;
//   "Silicone Compounds (Demo)" in D2-T12 -> the journey's supplier.
// What only time makes (a rating of a finished month) and the changes of D2-T14's precondition
// are prepared by meddevice_suite/qa/sheets/d2.py through the code's own services, and so are the
// records D2-T15 and D2-T16 start from: a supplier changed under a change request with QA's signed
// approval, for QA to read in the audit trail, and a supplier with QA's signed approval, a decision
// QA has prepared, and a second supplier to merge it into, which nobody merges, deletes or cancels.
//
// D2-T01 to D2-T11 build on one another, so once one fails the rest of that chain is not run;
// D2-T12 to D2-T16 stand on their own.

const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const SHEET = "meddevice_suite.qa.sheets.d2";
const API = /^https?:\/\/[^/]+\/api\//;
const FORM = ".page-container:visible";
const TIMEOUT = 20000; // the longest a page or an action may take
const QUIET_MS = 300; // no request in flight for this long: the page has finished
const RELOAD_DELAY_MS = 1000; // the desk reloads a form this long after the server changes it
const CHAIN_ENDS = 11; // D2-T01 .. D2-T11 build on one another

let password;
let users;
let journey; // the journey's masters (ui_journey.prepare_journey)
let data; // the sheet's own records (qa/sheets/d2.py)
const made = {}; // the records made along the way, by test

// What went wrong in the test: a refused or failed request, or an error message on screen, that
// the test did not expect. Every test ends by checking it is empty.
const run = { problems: [], expected: [] };

// --- Signing in -----------------------------------------------------------------------------

// Signs in as the job without writing the password into the command log, after leaving the
// previous person's page, as a person signs out before the next signs in (standard_flow.js).
function sign_in(job) {
	const email = users[job];
	leave_page();
	cy.session(["sheet-d2", email], () => {
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

// The Administrator who runs the tests, who holds every permission (D2-T16).
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

const ENTITIES = {
	"&amp;": "&",
	"&lt;": "<",
	"&gt;": ">",
	"&quot;": '"',
	"&#39;": "'",
	"&#x27;": "'",
};

// The text a person reads: tags dropped, entities as their characters, spaces collapsed.
function plain(html) {
	return String(html ?? "")
		.replace(/<[^>]*>/g, " ")
		.replace(/&(amp|lt|gt|quot|#39|#x27);/g, (entity) => ENTITIES[entity])
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
			const expected = run.expected.findIndex((text) => message.includes(text));
			if (expected >= 0) {
				run.expected.splice(expected, 1);
			} else {
				const path = new URL(req.url).pathname;
				run.problems.push(`${req.method} ${res.statusCode} ${path} → ${message}`);
			}
		});
	});
}

// The next refused request that says `message` is the one the step is about.
function expect_refusal(message) {
	cy.then(() => run.expected.push(message));
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
// form is not about to reload (standard_flow.js).
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
		message: plain(dialog.$wrapper.find(".msgprint").text()),
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

// The test went as expected: no error message on screen, no unexpected refusal, and every refusal
// the test expected has happened.
function no_problems(test) {
	settle();
	cy.window({ log: false }).then((win) => {
		const shown = message_dialog(win);
		if (shown?.error)
			run.problems.push(`the message "${shown.title}: ${shown.message}" is shown`);
		const problems = run.problems.splice(0);
		const missing = run.expected.splice(0);
		expect(problems, `${test}: problems\n${problems.join("\n")}\n`).to.be.empty;
		expect(missing, `${test}: refusals expected but not made`).to.be.empty;
	});
}

// The refusal is on screen as an error message saying exactly `message`, under `title` when the
// sheet names one; the person reads it and closes it.
function refused_with(title, message) {
	cy.window()
		.should((win) => {
			const shown = message_dialog(win);
			expect(shown, "an error message").to.not.equal(null);
			expect(shown.error, `"${shown.message}" is an error`).to.be.true;
			if (title) expect(shown.title, "the message's title").to.equal(title);
			expect(shown.message).to.equal(message);
		})
		.then({ timeout: 10000 }, (win) =>
			close_message(win).then((closed) => expect(closed, "the message closes").to.be.true)
		);
}

// --- Using the desk -------------------------------------------------------------------------

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

function open_list(doctype, filters = {}) {
	const query = new URLSearchParams(filters).toString();
	cy.visit(`/desk/${slug(doctype)}${query ? `?${query}` : ""}`);
	cy.window().should((win) => {
		expect(win.frappe.get_route()[0], "a list is open").to.equal("List");
		expect(win.cur_list?.doctype, "the list").to.equal(doctype);
	});
	settle();
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

function today_text(win) {
	return user_date(win, win.frappe.datetime.get_today());
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
	// Right after the person leaves the box it still shows the date while the record is empty, and
	// the refresh may land only then (found in CI, 2026-10-21: the box emptied after this check had
	// passed it). Wait until the record has the date or the box is empty; a box that keeps showing
	// a date the record never takes is a defect and fails here.
	const box_of = (win) => Cypress.$(win.document.body).find(selector).first().val();
	cy.window().should((win) => {
		expect(Boolean(read_stored(win)) || !box_of(win), `${label}: kept by the record`).to.be
			.true;
	});
	cy.window().then((win) => {
		if (read_stored(win)) return;
		cy.log(`${label}: date wiped by a form refresh, typed again`);
		if (reopen) reopen();
		cy.get(selector)
			.first()
			.then(($input) => type_into($input, user_date(win, value)));
	});
}

// Types into a link field and chooses the matching entry of its list.
function choose($input, value) {
	cy.wrap($input).clear().focus().type(value, { delay: 0 });
	// A record type shown by its title (a change request) has the title in bold and its name on
	// the line below; the person picks the entry that names the value.
	const names = (option) =>
		option.querySelector("strong")?.textContent.trim() === value ||
		// The name follows the title with no space between them in the entry's text.
		new RegExp(`(^|[^\\w-])${Cypress._.escapeRegExp(value)}([^\\w-]|$)`).test(
			option.textContent
		);
	cy.get('[role="listbox"]:visible [role="option"]')
		.filter((_, option) => names(option))
		.first()
		.click();
}

// A lookup's list left open lies over the fields below it; the person closes it before going on
// (standard_flow.js).
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

// Fills a field once the form has finished updating from the last one (standard_flow.js).
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
				else if (kind === "Date") type_into($input, user_date(win, value));
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

// Ticks or unticks a check box.
function tick(fieldname, on) {
	settle();
	close_open_lists();
	reveal(fieldname);
	cy.get(`${FORM} .frappe-control[data-fieldname="${fieldname}"] input[type="checkbox"]:visible`)
		.first()
		.then(($box) => {
			if ($box.prop("checked") !== on) cy.wrap($box).click();
		});
	cy.window().should((win) => expect(current(win)[fieldname], fieldname).to.equal(on ? 1 : 0));
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

// Ticks the row's box and presses the table's Delete button.
function delete_row(table, idx) {
	settle();
	close_open_lists();
	const grid = `${FORM} .frappe-control[data-fieldname="${table}"]`;
	cy.window().then((win) => {
		const left = rows(win, table).length - 1;
		cy.get(`${grid} .grid-body .grid-row[data-idx="${idx}"] .grid-row-check`).click();
		cy.get(`${grid} .grid-remove-rows`).should("be.visible").click();
		cy.window().should((w) => expect(rows(w, table)).to.have.length(left));
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

function take_action(action) {
	settle();
	cy.get(`${FORM} .actions-btn-group`).find("> button").click();
	cy.get(`${FORM} .actions-btn-group .dropdown-menu a.dropdown-item`)
		.contains(new RegExp(`^\\s*${action}\\s*$`))
		.click();
	// The approval engine's save makes the desk reload the form a second later (standard_flow.js).
	settle(RELOAD_DELAY_MS + QUIET_MS);
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

// Save, refused with the sheet's message; nothing is saved.
function save_refused(title, message) {
	expect_refusal(message);
	primary_action().should("contain", "Save").click();
	refused_with(title, message);
}

function open_dialog() {
	cy.get(".modal.show").should("have.css", "opacity", "1");
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the dialog has finished opening").to.equal(true);
		expect(win.cur_dialog.$wrapper.is(".modal.show"), "it is the dialog shown").to.equal(true);
	});
	return cy.get(".modal.show").last();
}

function submit() {
	primary_action().should("contain", "Submit").click();
	open_dialog().should("contain", "Permanently Submit");
	cy.get(".modal.show .btn-primary").contains("Yes").click();
	cy.window().should((win) => expect(current(win).docstatus, "submitted").to.equal(1));
	settle();
}

// --- Signing ----------------------------------------------------------------------------------

// The signing dialog of a signed act (compliance/esign.js), once it has finished opening.
function open_signing(label) {
	settle();
	button(label).click();
	signing_dialog_is(label);
}

function signing_dialog_is(label) {
	cy.get(".modal.show").should("have.css", "opacity", "1");
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the signing dialog is open").to.equal(true);
		expect(win.cur_dialog.title).to.equal(`Sign: ${label}`);
	});
}

// The reason, then the password, each once the dialog has taken it, as a person types them.
function enter_reason(reason) {
	cy.get('.modal.show textarea[data-fieldname="reason"]').click().clear().type(reason);
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
}

function enter_password(secret) {
	cy.get('.modal.show input[data-fieldname="password"]')
		.click()
		.clear()
		.type(secret, { log: false });
	cy.window()
		.its("cur_dialog")
		.should((dialog) =>
			expect(dialog.get_value("password"), "password entered").to.equal(secret)
		);
}

function press_sign() {
	cy.get(".modal:visible .btn-primary").contains("Sign").click();
	settle();
}

function sign(label, reason) {
	open_signing(label);
	enter_reason(reason);
	enter_password(password);
	press_sign();
}

// The person closes the signing dialog left open after a refusal, to correct the record.
function close_signing() {
	cy.get(".modal.show .btn-modal-close:visible").last().click();
	cy.get(".modal.show").should("not.exist");
	settle();
}

// A signature refused with `message`: the record stays a draft with no decision.
function sign_refused(label, reason, message) {
	open_signing(label);
	enter_reason(reason);
	enter_password(password);
	expect_refusal(message);
	press_sign();
	refused_with(null, message);
	close_signing();
	approval_still_draft();
}

function approval_still_draft() {
	cy.window().should((win) => {
		expect(current(win).docstatus, "still a draft").to.equal(0);
		expect(current(win).decision || null, "no decision").to.equal(null);
	});
	cy.window().then((win) =>
		stored("Supplier Approval", current(win).name).then((doc) => {
			expect(doc.docstatus, "stored as a draft").to.equal(0);
			expect(doc.decision || null, "no decision stored").to.equal(null);
		})
	);
}

// The certificate file, as the attach control uploads it.
function attach_certificate(name) {
	upload_private_file(name).then((url) =>
		cy.window().then((win) => win.cur_frm.set_value("certificate", url))
	);
	cy.window().should((win) => expect(current(win).certificate, "certificate").to.be.ok);
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

// QA opens a new decision from the supplier's form.
function decide_from_supplier(supplier) {
	open_form("Supplier", supplier);
	button("Decide Supplier Approval").click();
	form_is("Supplier Approval", (doc) => doc.__islocal && doc.supplier === supplier);
	settle();
}

// QA fills a decision with its certificate and items, as in D2-T04, and saves the draft.
function fill_decision(items, certificate) {
	fill("certificate_number", certificate);
	cy.window().then((win) => fill("certificate_expiry", in_days(win, 365), "Date"));
	attach_certificate(`${certificate}.pdf`);
	items.forEach((item, index) => {
		rows_up_to("items", index + 1);
		fill_row("items", index + 1, "item_code", item, "Link");
	});
	save();
}

// --- Reading -----------------------------------------------------------------------------------

// The record as stored, read as the signed-in user.
function stored(doctype, name) {
	return cy.call("frappe.client.get", { doctype, name }).then((r) => r.message);
}

// The form's message blocks of `colour` include exactly `text`.
function banner(colour, text) {
	cy.get(`${FORM} .form-message.${colour}`).should(($blocks) =>
		expect($blocks.toArray().map((block) => plain(block.textContent))).to.include(text)
	);
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
			expect(response.status, "refused").to.be.within(400, 499);
			return server_message(response.body);
		});
}

function in_days(win, days) {
	return win.frappe.datetime.add_days(win.frappe.datetime.get_today(), days);
}

// The electronic signatures shown on the form (esign.js), first signed first.
function signature_rows() {
	return cy.get(`${FORM} .mds-signatures`).last().find("tbody tr");
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

const SUPPLIER_KEPT = ["supplier_name", "supplier_group", "disabled", "mds_version", "modified"];
const APPROVAL_KEPT = [
	"supplier",
	"docstatus",
	"decision",
	"reason",
	"decided_by",
	"decided_at",
	"modified",
];

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

// A new purchase order on the screen: supplier, required-by date, the quarantine store, and lines.
function new_order(supplier, lines) {
	new_form("Purchase Order");
	fill("supplier", supplier, "Link");
	cy.window().then((win) => fill("schedule_date", in_days(win, 7), "Date"));
	fill("set_warehouse", journey.stores.quarantine, "Link");
	lines.forEach(([item, qty], index) => {
		rows_up_to("items", index + 1);
		fill_row("items", index + 1, "item_code", item, "Link");
		fill_row("items", index + 1, "qty", qty);
	});
}

// --- The sheet -----------------------------------------------------------------------------------

context("D2 Vendor Master: the browser test sheet, as each job", { retries: 0 }, () => {
	let chain_broken = false;
	const number = (title) => Number(/D2-T(\d+)/.exec(title)?.[1]);

	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		// The journey's raw materials, approved by the run's QA Manager (module 1).
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password })).then(({ message }) => {
			journey = message;
		});
		cy.then(() => cy.call(`${SHEET}.prepare`, { password })).then(({ message }) => {
			data = message;
		});
	});

	beforeEach(function () {
		if (chain_broken && number(this.currentTest.title) <= CHAIN_ENDS) this.skip();
		run.problems.length = 0;
		run.expected.length = 0;
		watch_requests();
	});

	afterEach(function () {
		if (this.currentTest.state === "failed" && number(this.currentTest.title) <= CHAIN_ENDS) {
			chain_broken = true;
		}
	});

	it("[URS-PUR-001] D2-T01 Purchase creates a supplier and sees it is not approved", () => {
		sign_in("Purchase");
		new_form("Supplier");
		fill("supplier_name", data.new_supplier);
		fill("supplier_group", data.group, "Link");
		save();
		cy.window().then((win) => {
			made.supplier = current(win).name;
			expect(current(win).mds_version, "Version").to.equal(1);
		});
		banner("red", "Not yet approved by QA: nothing can be ordered from this supplier.");
		offered("Decide Supplier Approval", false);
		no_problems("D2-T01");
	});

	it("[URS-PUR-001] D2-T02 A purchase order from a supplier with no QA decision is refused", () => {
		sign_in("Purchase");
		new_order(made.supplier, [[data.items.pouch, 1000]]);
		save_refused(
			"Supplier not approved",
			`Supplier ${made.supplier} is not approved by QA; nothing can be bought from it.`
		);
		cy.window().should((win) => expect(current(win).__islocal, "nothing was saved").to.be.ok);
		no_problems("D2-T02");
	});

	it("[URS-PUR-001] D2-T03 QA cannot approve without items or a valid certificate", () => {
		const reason = "Audit report reviewed (browser test sheet D2)";
		sign_in("QA Manager");
		decide_from_supplier(made.supplier);
		// Step 2: no items, no certificate.
		save();
		cy.window().then((win) => {
			made.approval = current(win).name;
		});
		sign_refused("Approve", reason, "List the items the supplier is approved for.");

		// Step 3: the same item twice.
		rows_up_to("items", 2);
		fill_row("items", 1, "item_code", data.items.pouch, "Link");
		fill_row("items", 2, "item_code", data.items.pouch, "Link");
		save();
		sign_refused(
			"Approve",
			reason,
			`Each item is listed once; listed again: ${data.items.pouch}.`
		);

		// Step 4: the item once, the certificate still empty.
		delete_row("items", 2);
		save();
		sign_refused(
			"Approve",
			reason,
			"Record the supplier's quality system certificate: its number, its expiry and the file."
		);

		// Step 5: a certificate that expired yesterday.
		fill("certificate_number", "TEST-CERT-1");
		cy.window().then((win) => fill("certificate_expiry", in_days(win, -1), "Date"));
		attach_certificate("TEST-CERT-1.pdf");
		save();
		cy.window().then((win) =>
			sign_refused(
				"Approve",
				reason,
				`The supplier's certificate expired on ${user_date(
					win,
					in_days(win, -1)
				)}; it cannot be approved on it.`
			)
		);
		no_problems("D2-T03");
	});

	it("[URS-PUR-001] D2-T04 QA approves the supplier for an item with a signature", () => {
		const reason = "Audited; certificate checked";
		sign_in("QA Manager");
		open_form("Supplier Approval", made.approval);
		cy.window().then((win) => fill("certificate_expiry", in_days(win, 365), "Date"));
		save();

		// Step 2: a wrong password.
		open_signing("Approve");
		enter_reason(reason);
		enter_password(`not-${password}`);
		expect_refusal("Incorrect password. The signature was not recorded.");
		press_sign();
		refused_with(null, "Incorrect password. The signature was not recorded.");
		approval_still_draft();

		// Step 3: the dialog is still open, with the reason; the correct password.
		signing_dialog_is("Approve");
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
		enter_password(password);
		press_sign();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.docstatus, "submitted").to.equal(1);
			expect(doc.decision).to.equal("Approved");
			expect(doc.decided_by).to.equal(users["QA Manager"]);
			expect(doc.decided_at?.slice(0, 10), "decided today").to.equal(
				win.frappe.datetime.get_today()
			);
		});
		banner("green", `Approved: ${reason}`);
		cy.get(`${FORM} .mds-signatures`).should("contain", "Approved").and("contain", reason);

		// Step 4: the supplier's form.
		open_form("Supplier", made.supplier);
		banner("green", `Approved by QA (${made.approval}) for: ${data.items.pouch}`);
		no_problems("D2-T04");
	});

	it("[URS-PUR-001] D2-T05 Purchase cannot prepare or sign a supplier decision", () => {
		sign_in("Purchase");
		open_list("Supplier Approval");
		cy.get(`${FORM} .page-actions`).should("be.visible");
		cy.get(`${FORM} .page-actions .primary-action:visible`).should("not.exist");

		cy.get(`${FORM} .list-row a.ellipsis[data-name="${made.approval}"]`).first().click();
		form_is("Supplier Approval", (doc) => doc.name === made.approval);
		settle();
		for (const label of ["Approve", "Approve Conditionally", "Not Approve"]) {
			offered(label, false);
		}
		primary_action().should("not.exist"); // no Submit, no Cancel
		cy.get(
			`${FORM} .frappe-control[data-fieldname="certificate_number"] input:visible`
		).should("not.exist");
		// Signing it through the API all the same is refused.
		refused_call("meddevice_suite.compliance.signing.sign_action", {
			doctype: "Supplier Approval",
			name: made.approval,
			action: "Not Approve",
			password,
			reason: "Not Purchase's decision (browser test sheet D2)",
		}).should("contain", `${made.approval} has been decided.`);
		stored("Supplier Approval", made.approval).then((doc) => {
			expect(doc.docstatus).to.equal(1);
			expect(doc.decision).to.equal("Approved");
		});
		no_problems("D2-T05");
	});

	it("[URS-PUR-001] D2-T06 A purchase order for an item the supplier is not approved for is refused", () => {
		sign_in("Purchase");
		new_order(made.supplier, [
			[data.items.pouch, 1000],
			[data.items.not_listed, 50],
		]);
		save_refused(
			"Supplier not approved",
			`Supplier ${made.supplier} is not approved for ${data.items.not_listed}; QA approves it for them before they are bought.`
		);
		delete_row("items", 2);
		save();
		cy.window().should((win) => {
			expect(current(win).mds_supplier_approval).to.equal(made.approval);
			expect(current(win).mds_conditional_reason || null).to.equal(null);
		});
		no_problems("D2-T06");
	});

	it("[URS-PUR-001] D2-T07 Conditional approval: end date rules on QA's decision", () => {
		const reason = "Trial decision (browser test sheet D2)";
		sign_in("QA Manager");
		decide_from_supplier(made.supplier);
		fill_decision([data.items.pouch], "ISO13485-TEST-D2-CONDITIONAL");
		cy.window().then((win) => {
			made.conditional = current(win).name;
		});

		// Step 2: no end date.
		sign_refused(
			"Approve Conditionally",
			reason,
			"Say until when the conditional approval holds."
		);
		// Step 3: today.
		cy.window().then((win) => fill("valid_until", win.frappe.datetime.get_today(), "Date"));
		save();
		sign_refused(
			"Approve Conditionally",
			reason,
			"A conditional approval holds until a day still to come."
		);
		// Step 4: an end date on a plain approval.
		cy.window().then((win) => {
			made.valid_until = in_days(win, 60);
			fill("valid_until", made.valid_until, "Date");
		});
		save();
		sign_refused(
			"Approve",
			reason,
			"Only a conditional approval has an end date; clear Conditional Until."
		);
		// Step 5.
		sign("Approve Conditionally", "New supplier on trial");
		cy.window().should((win) => {
			expect(current(win).docstatus, "submitted").to.equal(1);
			expect(current(win).decision).to.equal("Conditional");
		});
		cy.window().then((win) => {
			const until = user_date(win, made.valid_until);
			banner("orange", `Conditional until ${until}: New supplier on trial`);
			open_form("Supplier", made.supplier);
			banner(
				"orange",
				`Approved conditionally until ${until} (${made.conditional}) for: ${data.items.pouch}`
			);
		});
		no_problems("D2-T07");
	});

	it("[URS-PUR-001] D2-T08 An order to a conditionally approved supplier needs a reason", () => {
		sign_in("Purchase");
		new_order(made.supplier, [[data.items.pouch, 500]]);
		cy.window().then((win) =>
			save_refused(
				"Reason needed",
				`Supplier ${made.supplier} is approved only conditionally, until ${user_date(
					win,
					made.valid_until
				)}; say why this order is placed with it.`
			)
		);
		fill("mds_conditional_reason", "Only source of this pouch this month");
		save();
		cy.window().should((win) =>
			expect(current(win).mds_supplier_approval).to.equal(made.conditional)
		);
		cy.window().then((win) => {
			made.conditional_order = current(win).name;
		});
		no_problems("D2-T08");
	});

	it("[URS-PUR-001] D2-T09 A lot from a conditionally approved supplier is inspected in full", () => {
		// Precondition, on the screens: Purchase sends the order for approval, Management approves it.
		sign_in("Purchase");
		open_form("Purchase Order", made.conditional_order);
		take_action("Send for Approval");
		cy.window().should((win) =>
			expect(current(win).workflow_state).to.equal("Awaiting Approval")
		);
		sign_in("Management");
		open_form("Purchase Order", made.conditional_order);
		take_action("Approve");
		cy.window().should((win) => {
			expect(current(win).workflow_state).to.equal("Approved");
			expect(current(win).docstatus).to.equal(1);
		});

		// Stores receives it with the supplier's lot number and certificate.
		sign_in("Stores");
		open_form("Purchase Order", made.conditional_order);
		from_menu("Create", "Purchase Receipt");
		form_is("Purchase Receipt", (doc) => doc.__islocal);
		settle();
		cy.window().then((win) =>
			current(win).items.forEach((row) =>
				upload_private_file(`COA-TEST-D2-${row.item_code}.pdf`).then((url) =>
					cy.window().then((w) => {
						w.frappe.model.set_value(
							row.doctype,
							row.name,
							"mds_supplier_lot_no",
							`SUP-TEST-D2-${data.tag}`
						);
						w.frappe.model.set_value(row.doctype, row.name, "mds_certificate", url);
					})
				)
			)
		);
		cy.window().should((win) =>
			current(win).items.forEach((row) => expect(row.mds_certificate).to.be.ok)
		);
		save();
		// The receipt line: Inspect Each Lot in Full is ticked.
		cy.get(
			`${FORM} .frappe-control[data-fieldname="items"] .grid-row[data-idx="1"] .row-index`
		)
			.first()
			.click();
		cy.get(".grid-row-open").should("be.visible").and("contain", "Inspect Each Lot in Full");
		cy.window().should((win) => expect(current(win).items[0].mds_full_inspection).to.equal(1));
		// Closed with its collapse button: the heading of an editable line also holds its Delete.
		cy.get(".grid-row-open .grid-collapse-row:visible").first().click();
		cy.get(".grid-row-open").should("not.exist");
		submit();
		cy.window().then((win) => {
			made.receipt = current(win).name;
			cy.call(`${JOURNEY}.lots_of`, {
				doctype: "Purchase Receipt",
				name: made.receipt,
			}).then(({ message: lots }) => {
				expect(lots).to.have.length(1);
				expect(lots[0].qty).to.equal(500);
				made.lot = lots[0].batch_no;
			});
		});

		// As QC Inspector: the certificates, then the incoming inspection.
		sign_in("QC Inspector");
		// The receipt's name is known only once Stores has submitted it, above.
		cy.then(() => open_form("Purchase Receipt", made.receipt));
		button("Verify Certificates").click();
		open_dialog().should("contain", "Verify supplier certificates");
		cy.get(".modal.show .btn-primary").contains("Verify").click();
		settle();
		cy.window().should((win) =>
			expect(current(win).items[0].mds_certificate_verified).to.equal(1)
		);

		new_form("Quality Inspection");
		fill("inspection_type", "Incoming", "Select");
		fill("reference_type", "Purchase Receipt", "Select");
		cy.then(() => fill("reference_name", made.receipt, "Link"));
		fill("item_code", data.items.pouch, "Link");
		cy.window().should((win) =>
			expect(rows(win, "readings").length, "readings from the template").to.be.greaterThan(0)
		);
		cy.then(() => fill("batch_no", made.lot, "Link"));
		fill("sample_size", 1);
		cy.window().then((win) =>
			rows(win, "readings").forEach((reading, index) => {
				if (reading.numeric) {
					fill_row("readings", index + 1, "reading_1", "10.00");
					fill_row("readings", index + 1, "mds_instrument", journey.instrument, "Link");
				} else {
					fill_row("readings", index + 1, "reading_value", "Conforms");
				}
			})
		);
		cy.then(() =>
			save_refused(
				"Inspect the whole lot",
				`Lot ${made.lot} was ordered from a conditionally approved supplier and is inspected in full: the sample size is the lot's 500.`
			)
		);
		cy.window().should((win) => expect(current(win).__islocal, "nothing was saved").to.be.ok);

		fill("sample_size", 500);
		save();
		cy.window().should((win) => expect(current(win).sample_size).to.equal(500));
		no_problems("D2-T09");
	});

	it("[URS-PUR-001] D2-T10 QA stops a supplier and goods ordered earlier are refused at receipt", () => {
		// Precondition: an approved order not yet received, raised and approved by the two jobs
		// through the API.
		sign_in("Purchase");
		cy.window().then((win) =>
			cy
				.insert_doc("Purchase Order", {
					supplier: made.supplier,
					company: journey.company,
					schedule_date: in_days(win, 7),
					set_warehouse: journey.stores.quarantine,
					mds_conditional_reason: "Second lot of the trial (browser test sheet D2)",
					items: [
						{
							item_code: data.items.pouch,
							qty: 100,
							schedule_date: in_days(win, 7),
							warehouse: journey.stores.quarantine,
						},
					],
				})
				.then((order) => {
					made.open_order = order.name;
					cy.call("frappe.model.workflow.apply_workflow", {
						doc: order,
						action: "Send for Approval",
					});
				})
		);
		sign_in("Management");
		cy.then(() =>
			cy.call("frappe.model.workflow.apply_workflow", {
				doc: { doctype: "Purchase Order", name: made.open_order },
				action: "Approve",
			})
		);

		// QA decides Not Approve, with no items.
		sign_in("QA Manager");
		decide_from_supplier(made.supplier);
		save();
		sign("Not Approve", "Failed the re-audit");
		cy.window().should((win) => {
			expect(current(win).docstatus, "submitted").to.equal(1);
			expect(current(win).decision).to.equal("Not Approved");
		});
		cy.window().then((win) => {
			made.not_approved = current(win).name;
		});
		banner("red", "Not Approved: Failed the re-audit");
		cy.then(() => open_form("Supplier", made.supplier));
		cy.then(() =>
			banner(
				"red",
				`Not approved by QA (${made.not_approved}): nothing can be ordered from this supplier.`
			)
		);

		// Stores receives the order placed earlier: refused.
		sign_in("Stores");
		cy.then(() => open_form("Purchase Order", made.open_order));
		cy.window().should((win) => expect(current(win).docstatus).to.equal(1));
		from_menu("Create", "Purchase Receipt");
		form_is("Purchase Receipt", (doc) => doc.__islocal);
		settle();
		save_refused(
			"Supplier not approved",
			`Supplier ${made.supplier} is not approved by QA; nothing can be bought from it.`
		);
		cy.window().should((win) => expect(current(win).__islocal, "nothing was saved").to.be.ok);
		no_problems("D2-T10");
	});

	it("[URS-PUR-001] D2-T11 Not Approve with items listed is refused", () => {
		sign_in("QA Manager");
		decide_from_supplier(made.supplier);
		rows_up_to("items", 1);
		fill_row("items", 1, "item_code", data.items.pouch, "Link");
		save();
		sign_refused(
			"Not Approve",
			"Failed the re-audit (browser test sheet D2)",
			"A supplier that is not approved is approved for no items; remove the items first."
		);
		no_problems("D2-T11");
	});

	it("[URS-PUR-001] D2-T12 Goods cannot be received on a purchase invoice", () => {
		sign_in("Accounts");
		new_form("Purchase Invoice");
		fill("supplier", journey.supplier, "Link");
		tick("update_stock", true);
		fill("set_warehouse", journey.stores.quarantine, "Link");
		rows_up_to("items", 1);
		fill_row("items", 1, "item_code", data.items.silicone, "Link");
		fill_row("items", 1, "qty", 10);
		save_refused(
			"Receive on a goods receipt",
			"Goods are received on a goods receipt, not on a purchase invoice; untick Update Stock and book the invoice against the receipt."
		);
		cy.window().should((win) => expect(current(win).__islocal, "nothing was saved").to.be.ok);

		tick("update_stock", false);
		save();
		cy.window().should((win) => {
			expect(current(win).docstatus, "a draft").to.equal(0);
			expect(current(win).update_stock).to.equal(0);
		});
		no_problems("D2-T12");
	});

	it("[URS-PUR-002] D2-T13 A supplier rating is read-only and QA's decision closes its re-evaluation", () => {
		const rated = () => data.rated;
		sign_in("QA Manager");
		// Step 2: the list, and the rating due for re-evaluation.
		cy.then(() => open_list("Supplier Rating", { supplier: rated().supplier }));
		cy.then(() =>
			cy.get(`${FORM} .list-row a.ellipsis[data-name="${rated().rating}"]`).first().click()
		);
		cy.then(() => form_is("Supplier Rating", (doc) => doc.name === rated().rating));
		settle();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.standing).to.equal("Re-evaluation Due");
			expect(doc.lines.length, "order lines counted").to.be.greaterThan(0);
		});
		primary_action().should("not.exist"); // no Save, no Cancel
		// The ⋯ menu offers no Delete; Frappe draws its open list outside the button group.
		cy.get(`${FORM} .menu-btn-group > button`).first().click();
		cy.get(".dropdown-menu.show .dropdown-item")
			.should("have.length.greaterThan", 0)
			.then(($items) =>
				expect($items.toArray().map((item) => item.textContent.trim())).to.not.include(
					"Delete"
				)
			);
		cy.get(`${FORM} .menu-btn-group > button`).first().click();
		cy.get(".dropdown-menu.show").should("not.exist");
		// The Basis section is folded; the person opens it and scrolls to each figure.
		for (const field of ["lines", "quality_weight", "threshold", "settings_version"]) {
			reveal(field);
			cy.get(`${FORM} .frappe-control[data-fieldname="${field}"]`).should("be.visible");
		}
		cy.get(`${FORM} .frappe-control[data-fieldname="lines"] .grid-body .grid-row`).should(
			"have.length",
			1
		);

		// Step 3: the supplier's form.
		cy.then(() => open_form("Supplier", rated().supplier));
		cy.then(() =>
			banner(
				"red",
				`Rated ${rated().score} for ${rated().period}, below ${
					rated().threshold
				}: due for QA's re-evaluation (${rated().rating}).`
			)
		);

		// Step 4: QA decides the supplier again, as in D2-T04.
		button("Decide Supplier Approval").click();
		cy.then(() =>
			form_is(
				"Supplier Approval",
				(doc) => doc.__islocal && doc.supplier === rated().supplier
			)
		);
		settle();
		fill_decision([data.items.pouch], "ISO13485-TEST-D2-RE-EVALUATION");
		sign("Approve", "Audited; certificate checked");
		cy.window().should((win) => expect(current(win).docstatus, "submitted").to.equal(1));
		cy.window().then((win) => {
			made.re_evaluation = current(win).name;
		});

		// Step 5: the rating again.
		cy.then(() => open_form("Supplier Rating", rated().rating));
		cy.window().should((win) => {
			expect(current(win).standing).to.equal("Re-evaluated");
			expect(current(win).re_evaluation).to.equal(made.re_evaluation);
		});
		no_problems("D2-T13");
	});

	it("[URS-MST-001] D2-T14 A supplier in use changes only under an approved change request", () => {
		const supplier = () => data.in_use.supplier;
		sign_in("Purchase");
		cy.then(() => open_form("Supplier", supplier()));
		cy.window().then((win) => {
			made.version = current(win).mds_version;
			expect(current(win).supplier_group).to.equal(data.group);
		});

		// Step 2: a controlled field, with no change request.
		fill("supplier_group", data.other_group, "Link");
		cy.then(() =>
			save_refused(
				"Change control",
				`Supplier ${supplier()} is in use: name the approved change request that lists it to change it.`
			)
		);

		// Step 3: undone; only a contact detail.
		fill("supplier_group", data.group, "Link");
		fill("website", `https://test-d2-${data.tag}.example.com`);
		save();
		cy.window().should((win) => expect(current(win).mds_version).to.equal(made.version));

		// Step 5: the controlled field again, under the approved change request.
		fill("supplier_group", data.other_group, "Link");
		cy.then(() => fill("mds_change_request", data.in_use.change_request, "Link"));
		save();
		cy.window().should((win) => {
			const doc = current(win);
			expect(doc.supplier_group).to.equal(data.other_group);
			expect(doc.mds_version, "Version").to.equal(made.version + 1);
			expect(doc.mds_changed_under, "Last Changed Under").to.equal(
				data.in_use.change_request
			);
			expect(doc.mds_change_request || null, "Change Request for This Change").to.equal(
				null
			);
		});
		no_problems("D2-T14");
	});

	// 21 CFR 11.10(e): QA reads who changed a supplier in use, when, from what to what and under
	// which change request, and who approved the supplier with a signature, when and why, on the
	// forms and in the audit trail.
	it("[URS-PUR-001] [URS-CMP-012] D2-T15 QA reads the audit trail of a supplier and its approval", () => {
		const supplier = () => data.audited.supplier;
		const approval = () => data.audited.approval;
		const change = () => data.audited.change_request;
		const approved_because = () => data.audited.approval_reason;
		const purchase = () => data.full_names.Purchase;
		const qa = () => data.full_names["QA Manager"];
		const audit = {};
		sign_in("QA Manager");

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

		// Who, when, and the old and new values, on the entry's form.
		const event_shows = (doctype, name, action, user, full_name, values) => {
			cy.window().should((win) => {
				const doc = current(win);
				expect([doc.reference_doctype, doc.reference_name]).to.deep.equal([doctype, name]);
				expect(doc.action).to.equal(action);
				expect(doc.user, "who").to.equal(user);
				expect(doc.event_at.slice(0, 10), "when").to.equal(
					win.frappe.datetime.get_today()
				);
			});
			cy.get(`${FORM} .frappe-control[data-fieldname="action"]`).should("contain", action);
			cy.get(`${FORM} .frappe-control[data-fieldname="user"]`).should(
				($control) =>
					expect(
						[user, full_name].some((who) => $control.text().includes(who)),
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

		// On the supplier's form: who changed its group, when, the old and new value, and the
		// approved change request it was changed under.
		cy.then(() => open_form("Supplier", supplier()));
		cy.window().should((win) => {
			expect(current(win).supplier_group).to.equal(data.other_group);
			expect(current(win).mds_changed_under).to.equal(change());
		});
		cy.then(() =>
			audit_entry_shown([
				`Updated by ${purchase()}`,
				`Supplier Group: ${data.group} → ${data.other_group}`,
				`Last Changed Under: empty → ${change()}`,
			])
		);

		// Its whole audit trail, from the form's View menu, and the entry of the change opened.
		cy.then(() => open_audit_trail("Supplier", supplier(), ["Created", "Updated"]));
		cy.then(() => open_event("Supplier", supplier(), "Updated", change()));
		cy.then(() =>
			event_shows("Supplier", supplier(), "Updated", users.Purchase, purchase(), [
				"supplier_group",
				data.group,
				data.other_group,
				"mds_changed_under",
				change(),
			])
		);

		// Why: the approved change request the supplier was changed under.
		cy.then(() => open_form("Change Request", change()));
		cy.window().should((win) => {
			expect(current(win).status).to.equal("Approved");
			expect(current(win).reason).to.equal(data.audited.change_reason);
		});
		reveal("reason");
		cy.then(() =>
			cy
				.get(`${FORM} .frappe-control[data-fieldname="reason"]`)
				.should("contain", data.audited.change_reason)
		);

		// The supplier's approval: QA's Approved signature, who signed, when and why, and the
		// signed decision in its audit trail.
		cy.then(() => open_form("Supplier Approval", approval()));
		cy.then(() => banner("green", `Approved: ${approved_because()}`));
		cy.window().then((win) =>
			signature_rows().should(($rows) => {
				expect($rows).to.have.length(1);
				expect($rows.eq(0).text())
					.to.contain("Approved")
					.and.to.contain(qa())
					.and.to.contain(today_text(win))
					.and.to.contain(approved_because());
			})
		);
		cy.then(() => audit_entry_shown([`Submitted by ${qa()}`, "Decision: empty → Approved"]));
		cy.then(() => open_audit_trail("Supplier Approval", approval(), ["Created", "Submitted"]));
		cy.then(() =>
			open_event("Supplier Approval", approval(), "Submitted", approved_because())
		);
		cy.then(() =>
			event_shows("Supplier Approval", approval(), "Submitted", users["QA Manager"], qa(), [
				"decision",
				"Approved",
				approved_because(),
				"decided_by",
				users["QA Manager"],
			])
		);
		no_problems("D2-T15");
	});

	// ISO 13485 4.2.5, 7.4.1 (independent review 2026-10-15): a supplier is never merged into
	// another, which would move QA's decisions to a supplier QA never decided; and a decision on a
	// supplier is kept as signed: nobody, the Administrator included, deletes or cancels it, on the
	// form or past it.
	it("[URS-PUR-001] [URS-MST-017] [URS-CMP-016] D2-T16 A supplier is never merged, and its approval is never deleted or cancelled", () => {
		const supplier = () => data.kept.supplier;
		const duplicate = () => data.kept.duplicate;
		const approval = () => data.kept.approval;
		const draft = () => data.kept.draft;
		const never_deleted = /A supplier approval is never deleted\./;
		const never_cancelled =
			/A decision on a supplier is never cancelled; a new decision replaces it\./;
		// Frappe refuses to delete a submitted record before the approval's own rule is reached.
		const submitted_not_deleted = /Submitted Record cannot be deleted/;
		const before = {};
		const target = (name) => ({ doctype: "Supplier Approval", name });

		// Purchase, who keeps suppliers: Rename with "Merge with existing" into another supplier.
		sign_in("Purchase");
		cy.then(() => as_stored("Supplier", supplier(), SUPPLIER_KEPT)).then((kept) => {
			before.supplier = kept;
		});
		cy.then(() => as_stored("Supplier", duplicate(), SUPPLIER_KEPT)).then((kept) => {
			before.duplicate = kept;
		});
		cy.then(() => open_form("Supplier", supplier()));
		settle();
		cy.get(`${FORM} .menu-btn-group > button`).click();
		// Frappe draws the menu outside its button group: the visible menu's item, by its text.
		cy.get(".dropdown-menu.show")
			.contains(".dropdown-item", /^\s*Rename\s*$/)
			.click();
		open_dialog().should("contain", "New Name");
		cy.then(() => {
			cy.get('.modal.show input[data-fieldname="new_name"]').clear().type(duplicate());
			cy.get('.modal.show input[data-fieldname="merge"]').check();
			cy.window()
				.its("cur_dialog")
				.should((dialog) => {
					expect(dialog.get_value("new_name")).to.equal(duplicate());
					expect(dialog.get_value("merge")).to.equal(1);
				});
			const refusal = `Supplier ${supplier()} is not merged into ${duplicate()}: approvals and history stay with the record they were made for. Disable the duplicate instead.`;
			expect_refusal(refusal);
			cy.get(".modal.show .btn-primary").contains("Rename").click();
			refused_with(null, refusal);
			cy.call("frappe.client.get_count", {
				doctype: "Supplier",
				filters: { name: ["in", [supplier(), duplicate()]] },
			}).then(({ message }) => expect(message, "both suppliers kept").to.equal(2));
			cy.call("frappe.client.get_count", {
				doctype: "Supplier Approval",
				filters: { supplier: supplier(), docstatus: 1 },
			}).then(({ message }) =>
				expect(message, "QA's signed approval stays with it").to.equal(1)
			);
			cy.call("frappe.client.get_count", {
				doctype: "Supplier Approval",
				filters: { supplier: duplicate() },
			}).then(({ message }) =>
				expect(message, "no decision moved to the other supplier").to.equal(0)
			);
		});
		no_problems("D2-T16, Purchase");

		// QA, who decides suppliers: the form offers neither, and a call past the form is refused.
		sign_in("QA Manager");
		cy.then(() => as_stored("Supplier Approval", approval(), APPROVAL_KEPT)).then((kept) => {
			expect([kept.docstatus, kept.decision], "signed").to.deep.equal([1, "Approved"]);
			before.approval = kept;
		});
		cy.then(() => as_stored("Supplier Approval", draft(), APPROVAL_KEPT)).then((kept) => {
			expect([kept.docstatus, kept.decision || null], "prepared, not signed").to.deep.equal([
				0,
				null,
			]);
			before.draft = kept;
		});
		cy.then(() => open_form("Supplier Approval", approval()));
		cy.then(() => banner("green", `Approved: ${data.kept.approval_reason}`));
		offered("Cancel", false);
		in_menu("Delete", false);
		for (const method of ["frappe.client.delete", "frappe.client.cancel"]) {
			cy.then(() => refused_call(method, target(approval()))).then((message) =>
				expect(message, `${method} as QA`).to.match(/PermissionError/)
			);
		}
		cy.then(() => open_form("Supplier Approval", draft()));
		in_menu("Delete", false);
		cy.then(() => refused_call("frappe.client.delete", target(draft()))).then((message) =>
			expect(message, "deleting the decision QA prepared, as QA").to.match(/PermissionError/)
		);
		no_problems("D2-T16, QA");

		// The Administrator, who holds every permission.
		sign_in_as_administrator();
		cy.then(() => open_form("Supplier Approval", approval()));
		// No Delete on a submitted record; a call past the form is refused.
		in_menu("Delete", false);
		cy.then(() => refused_call("frappe.client.delete", target(approval()))).then((message) =>
			expect(message).to.match(submitted_not_deleted)
		);
		// No role holds cancel on a supplier approval, the System Manager included: the form offers
		// no Cancel, not even to the Administrator, and a call past the form is refused by the
		// approval's own rule.
		offered("Cancel", false);
		cy.then(() => refused_call("frappe.client.cancel", target(approval()))).then((message) =>
			expect(message).to.match(never_cancelled)
		);
		// The decision QA prepared: no role holds delete, so the menu offers it to nobody, not even
		// the Administrator; a call past the form is refused by the approval's own rule.
		cy.then(() => open_form("Supplier Approval", draft()));
		in_menu("Delete", false);
		cy.then(() => refused_call("frappe.client.delete", target(draft()))).then((message) =>
			expect(message).to.match(never_deleted)
		);

		// Both suppliers and both decisions are still there, as they were.
		cy.then(() => as_stored("Supplier", supplier(), SUPPLIER_KEPT)).then((kept) =>
			expect(kept, "the supplier, unchanged").to.deep.equal(before.supplier)
		);
		cy.then(() => as_stored("Supplier", duplicate(), SUPPLIER_KEPT)).then((kept) =>
			expect(kept, "the other supplier, unchanged").to.deep.equal(before.duplicate)
		);
		cy.then(() => as_stored("Supplier Approval", approval(), APPROVAL_KEPT)).then((kept) =>
			expect(kept, "the signed approval, unchanged").to.deep.equal(before.approval)
		);
		cy.then(() => as_stored("Supplier Approval", draft(), APPROVAL_KEPT)).then((kept) =>
			expect(kept, "the decision QA prepared, unchanged").to.deep.equal(before.draft)
		);
		no_problems("D2-T16");
	});
});

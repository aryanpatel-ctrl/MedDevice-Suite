// The device master record (module 31) on the screen: the Production Supervisor signs a revision as
// its author and QA approves it on the record's form, each with a signature; a revised
// specification puts it out of date in the same transaction, and the next revision, started from the
// form, is signed and approved again (meddevice_suite/qms/device_master_record.py).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const DMR = "Device Master Record";
const FORM = ".page-container:visible";

let password, qa, supervisor, record, item, specification;

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

function open_record(name) {
	cy.visit(`/desk/device-master-record/${encodeURIComponent(name)}`);
	cy.window().should((win) => expect(win.cur_frm?.doc?.name).to.equal(name));
}

function sign(name, button, title) {
	open_record(name);
	cy.get(`${FORM} button[data-label="${encodeURIComponent(button)}"]:visible`).click();
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
		expect(win.cur_dialog.title).to.equal(title);
	});
	cy.get('.modal.show input[data-fieldname="password"]').click().type(password, { log: false });
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("password")).to.equal(password));
	cy.get(".modal:visible .btn-primary").contains("Sign").click();
}

function author_and_approve(name) {
	as_user(supervisor);
	sign(name, "Sign as Author", "Sign: Author");
	cy.window().should((win) => expect(win.cur_frm.doc.status).to.equal("Awaiting Approval"));

	as_user(qa);
	open_record(name);
	cy.get(`${FORM} button[data-label="Approve"]:visible`).should("exist");
	sign(name, "Approve", "Sign: Approve");
	cy.window().should((win) => {
		expect(win.cur_frm.doc.docstatus, "approved").to.equal(1);
		expect(win.cur_frm.doc.status).to.equal("Current");
	});
}

context("Device master record", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			qa = message.users["QA Manager"];
			supervisor = message.users["Production Supervisor"];
		});
		cy.then(() =>
			cy
				.call(`${JOURNEY}.prepare_device_master_record`, { password })
				.then(({ message }) => {
					({ record, item, specification } = message);
				})
		);
	});

	it("[URS-QMS-028] [URS-QMS-029] spec revised → out of date → re-approved", () => {
		// The author does not approve their own revision: no Approve button for them.
		as_user(supervisor);
		open_record(record);
		cy.get(`${FORM} button[data-label="Approve"]`).should("not.exist");
		author_and_approve(record);
		cy.window().should((win) => {
			const pinned = win.cur_frm.doc.elements.find(
				(row) => row.reference_name === specification
			);
			expect(pinned?.pinned_version, "the specification's version pinned").to.be.ok;
		});

		// QA revises the final specification under an approved change request.
		as_user("Administrator");
		cy.call(`${JOURNEY}.revise_dmr_specification`, { password });

		as_user(qa);
		open_record(record);
		cy.window().should((win) => {
			expect(win.cur_frm.doc.status).to.equal("Out of Date");
			const stale = win.cur_frm.doc.elements.filter((row) => row.stale);
			expect(stale.map((row) => row.reference_name)).to.deep.equal([specification]);
			expect(stale[0].current_changed_under, "the change request it was made under").to.be
				.ok;
		});
		cy.get(`${FORM} .form-message`).should("contain", "Out of date since"); // the headline alert

		// The Production Supervisor starts the next revision from the form; it is signed again.
		as_user(supervisor);
		open_record(record);
		cy.get(
			`${FORM} button[data-label="${encodeURIComponent("Start New Revision")}"]:visible`
		).click();
		cy.window().should((win) => {
			expect(win.cur_frm?.doc?.doctype).to.equal(DMR);
			expect(win.cur_frm.doc.name).not.to.equal(record);
			expect(win.cur_frm.doc.status).to.equal("Draft");
			expect(win.cur_frm.doc.supersedes).to.equal(record);
		});
		cy.window().then((win) => {
			const next = win.cur_frm.doc.name;
			author_and_approve(next);
			cy.call("frappe.client.get_value", {
				doctype: DMR,
				filters: { name: record },
				fieldname: ["status", "superseded_by"],
			}).then(({ message }) => {
				expect(message.status).to.equal("Superseded");
				expect(message.superseded_by).to.equal(next);
			});
			cy.call("frappe.client.get_value", {
				doctype: DMR,
				filters: { item, status: "Current" },
				fieldname: "name",
			}).then(({ message }) => expect(message.name).to.equal(next));
		});
	});
});

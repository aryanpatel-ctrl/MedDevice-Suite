// Regulatory licences (module 41) on the screen: QA verifies a licence of the plant on its form
// with a signature, which pins its copy (meddevice_suite/qms/regulatory_licence.py).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const FORM = ".page-container:visible";

let password, qa, licence;

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

context("Regulatory licences", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			qa = message.users["QA Manager"];
		});
	});

	it("[URS-REG-001] QA verifies a licence on its form with a signature", () => {
		as_user(qa);
		cy.call(`${JOURNEY}.draft_licence`).then(({ message }) => {
			licence = message;
			cy.visit(`/desk/regulatory-licence/${encodeURIComponent(licence)}`);
		});
		cy.window().should((win) => expect(win.cur_frm?.doctype).to.equal("Regulatory Licence"));
		cy.get(`${FORM} button[data-label="Verify"]:visible`).click();
		cy.window().should((win) => {
			expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
			expect(win.cur_dialog.title).to.equal("Sign: Verify");
		});
		const reason = "Checked against the authority's original (browser test)";
		cy.get('.modal.show textarea[data-fieldname="reason"]').click().type(reason);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
		cy.get('.modal.show input[data-fieldname="password"]')
			.click()
			.type(password, { log: false });
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("password")).to.equal(password));
		cy.get(".modal:visible .btn-primary").contains("Sign").click();
		cy.window().should((win) => {
			expect(win.cur_frm.doc.docstatus, "verified").to.equal(1);
			expect(win.cur_frm.doc.status).to.equal("Verified");
			expect(win.cur_frm.doc.verified_file_sha256, "the copy pinned").to.have.length(64);
		});
	});
});

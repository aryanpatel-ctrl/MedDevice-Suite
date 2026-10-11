// Training (module 37) on the screen: the operator signs on their training record that they were
// trained, and the Production Supervisor, its named assessor, signs them competent, each on the
// record's form with a signature (meddevice_suite/qms/training.py).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const FORM = ".page-container:visible";

let password, operator, supervisor, record;

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

function sign(button, title) {
	cy.visit(`/desk/training-record/${encodeURIComponent(record)}`);
	cy.window().should((win) => expect(win.cur_frm?.doc?.name).to.equal(record));
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

context("Training", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			operator = message.users["Operator"];
			supervisor = message.users["Production Supervisor"];
		});
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password }));
		cy.then(() =>
			cy.call(`${JOURNEY}.prepare_training`, { password }).then(({ message }) => {
				record = message.record;
			})
		);
	});

	it("[URS-TRN-002] the operator signs that they were trained, then the supervisor signs them competent", () => {
		as_user(operator);
		sign("Sign: Trained", "Sign: Trained");
		cy.window().should((win) => expect(win.cur_frm.doc.status).to.equal("Trained"));

		as_user(supervisor);
		sign("Competent", "Sign: Competent");
		cy.window().should((win) => {
			expect(win.cur_frm.doc.docstatus, "completed").to.equal(1);
			expect(win.cur_frm.doc.status).to.equal("Competent");
		});
	});
});

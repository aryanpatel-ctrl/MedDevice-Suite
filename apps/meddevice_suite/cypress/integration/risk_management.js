// Risk management (module 39) on the screen: a hazard with its control is signed by its author (a
// QA person), its reviewer (the Production Supervisor) and QA's approver, a third person; then the
// risk management report on it is signed by its author and approved by QA, each on the record's
// form with a signature (meddevice_suite/qms/risk_management.py).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const FORM = ".page-container:visible";

let password, qa, supervisor, author, file, assessment, sop;

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

function sign(route, name, button, title) {
	cy.visit(`/desk/${route}/${encodeURIComponent(name)}`);
	cy.window().should((win) => expect(win.cur_frm?.doc?.name).to.equal(name));
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

context("Risk management", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			qa = message.users["QA Manager"];
			supervisor = message.users["Production Supervisor"];
		});
		cy.then(() =>
			cy.call(`${JOURNEY}.prepare_risk_management`, { password }).then(({ message }) => {
				({ file, assessment, author, sop } = message);
			})
		);
	});

	it("[URS-QMS-020] hazard → control → report, each step signed on its form", () => {
		as_user(author);
		sign("risk-assessment", assessment, "Sign as Author", "Sign: Author");
		cy.window().should((win) => expect(win.cur_frm.doc.status).to.equal("In Review"));
		cy.window().should((win) =>
			expect(
				win.cur_frm.doc.hazards[0].initial_acceptability,
				"worked out, not typed"
			).to.equal("Unacceptable")
		);

		as_user(supervisor);
		sign("risk-assessment", assessment, "Sign as Reviewer", "Sign: Review");
		cy.window().should((win) => expect(win.cur_frm.doc.status).to.equal("Awaiting Approval"));

		as_user(qa);
		sign("risk-assessment", assessment, "Approve", "Sign: Approve");
		cy.window().should((win) => {
			expect(win.cur_frm.doc.docstatus, "approved").to.equal(1);
			expect(win.cur_frm.doc.status).to.equal("Approved");
			expect(win.cur_frm.doc.controls[0].pinned_version, "the control's version pinned").to
				.be.ok;
		});

		as_user(author);
		cy.insert_doc("Risk Management Report", {
			risk_assessment: assessment,
			plan_implemented: 1,
			plan_review: "Each activity of the plan checked against its record (browser test).",
			overall_conclusion:
				"The residual risks are as low as reasonably practicable and outweighed by the benefits of drainage.",
			information_sources: [{ controlled_document: sop }],
		}).then((report) => {
			sign("risk-management-report", report.name, "Sign as Author", "Sign: Author");
			cy.window().should((win) =>
				expect(win.cur_frm.doc.status).to.equal("Awaiting Approval")
			);

			as_user(qa);
			sign("risk-management-report", report.name, "Approve", "Sign: Approve");
			cy.window().should((win) => {
				expect(win.cur_frm.doc.docstatus, "approved").to.equal(1);
				expect(win.cur_frm.doc.status).to.equal("Approved");
			});
			cy.call("frappe.client.get_value", {
				doctype: "Risk Management File",
				filters: { name: file },
				fieldname: ["current_revision", "current_report"],
			}).then(({ message }) => {
				expect(message.current_revision).to.equal(assessment);
				expect(message.current_report).to.equal(report.name);
			});
		});
	});
});

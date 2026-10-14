// Training (module 37) on the screen: the operator signs on their training record that they were
// trained, and the Production Supervisor, its named assessor, signs them competent, each on the
// record's form with a signature; QA enters a training given on paper before the system, started
// from the list, with its certificate and a signature (meddevice_suite/qms/training.py).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const FORM = ".page-container:visible";

let password, operator, supervisor, qa, record, history;

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

// Waits for the dialog that has just opened to finish opening, as a person would before using it.
function dialog_open(title) {
	cy.get(".modal.show").should("have.css", "opacity", "1");
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the dialog has opened").to.equal(true);
		expect(win.cur_dialog.title).to.equal(title);
	});
}

function sign(button, title, name = record, reason = null) {
	cy.visit(`/desk/training-record/${encodeURIComponent(name)}`);
	cy.window().should((win) => expect(win.cur_frm?.doc?.name).to.equal(name));
	cy.get(`${FORM} button[data-label="${encodeURIComponent(button)}"]:visible`).click();
	dialog_open(title);
	if (reason) {
		cy.get('.modal.show textarea[data-fieldname="reason"]').click().type(reason);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
	}
	cy.get('.modal.show input[data-fieldname="password"]').click().type(password, { log: false });
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value("password")).to.equal(password));
	cy.get(".modal:visible .btn-primary").contains("Sign").click();
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

context("Training", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			operator = message.users["Operator"];
			supervisor = message.users["Production Supervisor"];
			qa = message.users["QA Manager"];
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

	it("[URS-TRN-002] QA starts the record of an operator's paper training from the list, attaches the certificate and signs it", () => {
		// Prepared here, not with the other records: adding a document to the job's matrix is a
		// cancel and amend, which withdraws the job's open training, the other test's included.
		as_user("Administrator");
		cy.call(`${JOURNEY}.prepare_training_history`, { password }).then(({ message }) => {
			history = message;
			expect(history.opened, "the system opened the operator's training").to.be.ok;
		});

		as_user(qa);
		cy.visit("/desk/training-record");
		cy.get(`${FORM} .page-actions .primary-action:visible`).click();
		dialog_open("Record Training History");
		cy.window().then((win) => win.cur_dialog.set_value("employee", history.employee));
		cy.window().then((win) =>
			win.cur_dialog.set_value("controlled_document", history.document)
		);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => {
				expect(dialog.get_value("employee")).to.equal(history.employee);
				expect(dialog.get_value("controlled_document")).to.equal(history.document);
			});
		cy.get(".modal.show .btn-primary").contains("Start").click();
		cy.window().should((win) => {
			const doc = win.cur_frm?.doc;
			expect(doc?.doctype, "the new record's form").to.equal("Training Record");
			expect(doc.__islocal, "saved as a draft").to.not.be.ok;
			expect(doc.employee).to.equal(history.employee);
			expect(doc.controlled_document).to.equal(history.document);
			expect(doc.trigger).to.equal("Historical");
			expect(doc.status).to.equal("Open");
			history.record = doc.name;
		});

		// How and when the training was given, with its certificate, as the paper file shows.
		cy.window().then((win) =>
			win.cur_frm.set_value({
				training_method: "Instructor-led",
				training_date: win.frappe.datetime.add_days(win.frappe.datetime.get_today(), -200),
				trainer: "QA trainer (paper record)",
			})
		);
		upload_private_file("training-certificate.pdf").then((url) =>
			cy.window().then((win) => win.cur_frm.set_value("certificate", url))
		);
		cy.window().should((win) =>
			expect(win.cur_frm.doc.certificate, "certificate").to.match(/^\/private\/files\//)
		);
		cy.get(`${FORM} .page-actions .primary-action:visible`).should("contain", "Save").click();
		cy.window().should((win) => {
			expect(win.cur_frm.is_dirty(), "saved").to.not.be.ok;
			expect(win.cur_frm.doc.training_method).to.equal("Instructor-led");
		});

		cy.then(() =>
			sign(
				"Record History",
				"Sign: Record History",
				history.record,
				"Transcribed from the paper training file (browser test)"
			)
		);
		cy.window().should((win) => {
			expect(win.cur_frm.doc.docstatus, "completed").to.equal(1);
			expect(win.cur_frm.doc.status).to.equal("Competent");
			expect(win.cur_frm.doc.is_historical, "entered from paper").to.equal(1);
		});
		// The record the system had opened for the same training no longer asks the operator to sign.
		cy.then(() =>
			cy
				.call("frappe.client.get_value", {
					doctype: "Training Record",
					filters: { name: history.opened },
					fieldname: "status",
				})
				.then(({ message }) => expect(message.status).to.equal("Withdrawn"))
		);
	});
});

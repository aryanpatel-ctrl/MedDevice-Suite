// Document control as its people use it (module 30): each person signs in as themselves,
// sees only the act that is theirs, and signs it in the signing dialog with their own password.
const PASSWORD = `Dc!${Date.now()}${Math.random().toString(36).slice(2)}A9`;
const PEOPLE = {
	author: ["dc.author@ui-tests.example.com", "Production Supervisor"],
	reviewer: ["dc.reviewer@ui-tests.example.com", "QC Inspector"],
	qa: ["dc.qa@ui-tests.example.com", "QA Manager"],
	outsider: ["dc.outsider@ui-tests.example.com", "Sales"],
};
const email = (who) => PEOPLE[who][0];
let revision;

function as(who) {
	cy.login(email(who), PASSWORD);
	cy.visit("/desk");
}

function upload_private_pdf() {
	return cy.fixture("controlled-document.pdf", null).then((pdf) =>
		cy.window().then((win) => {
			const form = new win.FormData();
			form.append(
				"file",
				new win.Blob([pdf], { type: "application/pdf" }),
				"controlled-document.pdf"
			);
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

function open_revision() {
	cy.visit(`/desk/document-revision/${revision}`);
	cy.window().its("cur_frm.doc.name").should("eq", revision);
}

// As a person would: once the signing dialog has finished opening. Frappe makes it the
// current dialog after its fade-in ends, when it also focuses its first field.
function signing_dialog_open() {
	cy.get(".modal.show").should("have.css", "opacity", "1");
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the signing dialog is open").to.equal(true);
		expect(win.cur_dialog.title).to.match(/^Sign: /);
	});
}

function sign(button) {
	cy.get(`.page-container:visible button[data-label="${encodeURIComponent(button)}"]`).click();
	signing_dialog_open();
	cy.get('.modal.show input[data-fieldname="password"]').click().type(PASSWORD, { log: false });
	// Frappe takes a typed value a moment after the keystrokes; sign once the dialog has it.
	cy.window()
		.its("cur_dialog")
		.should((dialog) =>
			expect(dialog.get_value("password"), "password entered").to.equal(PASSWORD)
		);
	cy.get(".modal:visible .btn-primary").contains("Sign").click();
}

function status_is(status) {
	cy.window().its("cur_frm.doc.status").should("eq", status);
}

function offered(button, expected) {
	cy.get(".page-container:visible .page-actions").should("be.visible");
	cy.get(`.page-container:visible button[data-label="${encodeURIComponent(button)}"]`).should(
		expected ? "exist" : "not.exist"
	);
}

context("Document control", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		for (const [user, profile] of Object.values(PEOPLE)) {
			cy.call("frappe.client.get_count", { doctype: "User", filters: { name: user } }).then(
				(r) => {
					if (!r.message) {
						cy.call("frappe.client.insert", {
							doc: {
								doctype: "User",
								email: user,
								first_name: profile,
								last_name: "(browser test)",
								send_welcome_email: 0,
								role_profiles: [{ role_profile: profile }],
							},
						});
					}
					cy.call("frappe.client.set_value", {
						doctype: "User",
						name: user,
						fieldname: "new_password",
						value: PASSWORD,
					});
				}
			);
		}
	});

	it("[URS-QMS-001] QA registers a document and its owner drafts the first revision", () => {
		as("qa");
		cy.call("frappe.client.insert", {
			doc: {
				doctype: "Controlled Document",
				title: "Browser test procedure",
				document_type: "Work Instruction",
				department: "PRD",
				process_owner: email("author"),
			},
		}).then((r) => {
			const document = r.message.name;
			as("author");
			upload_private_pdf().then((file) =>
				cy
					.call("frappe.client.insert", {
						doc: {
							doctype: "Document Revision",
							controlled_document: document,
							file,
							reviewer: email("reviewer"),
						},
					})
					.then((saved) => {
						revision = saved.message.name;
					})
			);
		});
	});

	it("[URS-CMP-030] [URS-QMS-002] the author signs from the form; only the author is offered it", () => {
		as("reviewer");
		open_revision();
		offered("Sign as Author", false);

		as("author");
		open_revision();
		offered("Sign as Reviewer", false);
		sign("Sign as Author");
		status_is("In Review");
	});

	it("[URS-QMS-005] someone outside the revision cannot see it", () => {
		as("outsider");
		cy.call("frappe.client.get_count", {
			doctype: "Document Revision",
			filters: { name: revision },
		}).then((r) => expect(r.message).to.equal(0));
	});

	it("[URS-CMP-030] [URS-QMS-002] the reviewer signs, then QA approves; nobody can just submit", () => {
		as("reviewer");
		open_revision();
		sign("Sign as Reviewer");
		status_is("Awaiting Approval");

		as("qa");
		open_revision();
		cy.get(".page-container:visible .primary-action").should("not.contain", "Submit");
		sign("Approve");
		status_is("Effective");
		cy.contains(".page-container:visible", "This is the revision in use").should("be.visible");
		cy.get(".page-container:visible .page-head .indicator-pill").should(
			"contain",
			"Effective"
		);
	});

	it("[URS-QMS-001] the process owner is offered a new revision; others are not", () => {
		cy.window()
			.its("frappe")
			.then(() =>
				cy.call("frappe.client.get_value", {
					doctype: "Document Revision",
					filters: { name: revision },
					fieldname: "controlled_document",
				})
			)
			.then((r) => {
				const document = r.message.controlled_document;
				as("outsider");
				cy.visit(`/desk/controlled-document/${document}`);
				cy.window().its("cur_frm.doc.name").should("eq", document);
				offered("Effective Revision", true);
				offered("New Revision", false);

				as("author");
				cy.visit(`/desk/controlled-document/${document}`);
				cy.window().its("cur_frm.doc.name").should("eq", document);
				offered("New Revision", true);
			});
	});

	it("[URS-QMS-008] [URS-CMP-003] the effective revision prints as a controlled copy with its signatures", () => {
		as("qa");
		cy.visit(
			`/printview?doctype=Document%20Revision&name=${revision}&format=Controlled%20Copy&no_letterhead=1`
		);
		cy.get(".print-format")
			.should("contain", "CONTROLLED COPY")
			.and("contain", email("author"))
			.and("contain", email("reviewer"))
			.and("contain", email("qa"))
			.and("contain", "Approved");
	});

	it("[URS-QMS-006] QA withdraws the document with a signature and a reason", () => {
		as("qa");
		cy.call("frappe.client.get_value", {
			doctype: "Document Revision",
			filters: { name: revision },
			fieldname: "controlled_document",
		}).then((r) => {
			const document = r.message.controlled_document;
			cy.visit(`/desk/controlled-document/${document}`);
			cy.window().its("cur_frm.doc.name").should("eq", document);
			cy.get(
				'.page-container:visible .inner-group-button[data-label="Actions"] button'
			).click();
			cy.get('.page-container:visible .dropdown-item[data-label="Withdraw"]').click();
			signing_dialog_open();
			cy.get('.modal.show textarea[data-fieldname="reason"]').type(
				"Replaced by a new procedure"
			);
			// One field after the other, as a person fills it in.
			cy.window()
				.its("cur_dialog")
				.should((dialog) =>
					expect(dialog.get_value("reason")).to.equal("Replaced by a new procedure")
				);
			cy.get('.modal.show input[data-fieldname="password"]')
				.click()
				.type(PASSWORD, { log: false });
			// Sign once the dialog has both values.
			cy.window()
				.its("cur_dialog")
				.should((dialog) => {
					expect(dialog.get_value("reason")).to.equal("Replaced by a new procedure");
					expect(dialog.get_value("password")).to.equal(PASSWORD);
				});
			cy.get(".modal:visible .btn-primary").contains("Sign").click();

			cy.window().its("cur_frm.doc.status").should("eq", "Obsolete");
			cy.contains(".page-container:visible", "Replaced by a new procedure").should(
				"be.visible"
			);
			offered("New Revision", false);
		});
	});
});

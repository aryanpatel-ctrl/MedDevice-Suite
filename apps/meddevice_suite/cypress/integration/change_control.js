// Change control as its people use it (module 36): each person signs in as themselves, is offered
// only the acts that are theirs, and signs in the signing dialog with their own password.
const PASSWORD = `Cc!${Date.now()}${Math.random().toString(36).slice(2)}A9`;
const PEOPLE = {
	requester: ["cc.requester@ui-tests.example.com", "Production Supervisor"],
	reviewer: ["cc.reviewer@ui-tests.example.com", "QC Inspector"],
	qa: ["cc.qa@ui-tests.example.com", "QA Manager"],
	manager: ["cc.manager@ui-tests.example.com", "Management"],
	stores: ["cc.stores@ui-tests.example.com", "Stores"],
	outsider: ["cc.outsider@ui-tests.example.com", "Sales"],
};
const CHANGE_CONTROL = "meddevice_suite.qms.change_control";
const FORM = ".page-container:visible";
const email = (who) => PEOPLE[who][0];
let document_name;
let change;

// Signs in as `who`, leaving the previous person's page first, as a person signs out before the
// next signs in: a page left open while the sessions change can end the new session on a slow
// machine (found in CI, 2026-10-08; standard_flow.js sign_in does the same).
function as(who) {
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	cy.login(email(who), PASSWORD);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", email(who));
}

function as_call(who, method, args) {
	as(who);
	return cy.call(method, args);
}

function open_change(name = change) {
	cy.visit(`/desk/change-request/${name}`);
	cy.window().its("cur_frm.doc.name").should("eq", name);
	cy.get(`${FORM} .page-actions`).should("be.visible");
}

function status_is(status) {
	cy.window().its("cur_frm.doc.status").should("eq", status);
}

function button(label) {
	return cy.get(`${FORM} button[data-label="${encodeURIComponent(label)}"]`);
}

function offered(label, expected) {
	button(label).should(expected ? "exist" : "not.exist");
}

// As a person would: once the dialog has finished opening (Frappe then moves the focus to its
// first field), each value typed and taken by the dialog before the next.
function dialog_open(title) {
	cy.get(".modal.show").should("have.css", "opacity", "1");
	cy.window().should((win) => {
		expect(win.cur_dialog?.display, "the dialog has finished opening").to.equal(true);
		expect(win.cur_dialog.title).to.equal(title);
	});
}

function type_in_dialog(selector, fieldname, text, options = {}) {
	cy.get(`.modal.show ${selector}[data-fieldname="${fieldname}"]`)
		.click()
		.type(text, { log: !options.secret });
	cy.window()
		.its("cur_dialog")
		.should((dialog) => expect(dialog.get_value(fieldname), fieldname).to.equal(text));
}

function sign(label, title, reason) {
	button(label).click();
	dialog_open(title);
	if (reason) type_in_dialog("textarea", "reason", reason);
	type_in_dialog("input", "password", PASSWORD, { secret: true });
	cy.get(".modal:visible .btn-primary").contains("Sign").click();
}

function upload_private_pdf() {
	return cy.fixture("controlled-document.pdf", null).then((pdf) =>
		cy.window().then((win) => {
			const form = new win.FormData();
			form.append("file", new win.Blob([pdf], { type: "application/pdf" }), "sop.pdf");
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

// Raises a change request through the API as `who`; an action due "today" is due on the site's
// today, which can differ from the test machine's.
function raise(who, title, actions) {
	as(who);
	return cy.window().then((win) => {
		const today = win.frappe.datetime.get_today();
		const dated = actions.map((row) => ({
			...row,
			due_date: row.due_date === "today" ? today : row.due_date,
		}));
		return insert_change(title, dated);
	});
}

function insert_change(title, actions) {
	return cy
		.call("frappe.client.insert", {
			doc: {
				doctype: "Change Request",
				title,
				change_type: "Document",
				description: "Add the hardness check to the incoming inspection",
				reason: "A supplier lot passed with the wrong hardness",
				affected_records: [
					{ reference_doctype: "Controlled Document", reference_name: document_name },
				],
				actions,
			},
		})
		.then((r) => r.message.name);
}

context("Change control", () => {
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
		// A procedure in effect, owned by the requester: its next revision needs a change.
		as_call("qa", "frappe.client.insert", {
			doc: {
				doctype: "Controlled Document",
				title: "Change control browser test",
				document_type: "SOP",
				department: "QC",
				process_owner: email("requester"),
			},
		}).then((r) => {
			document_name = r.message.name;
			as("requester");
			upload_private_pdf().then((file) =>
				cy
					.call("frappe.client.insert", {
						doc: {
							doctype: "Document Revision",
							controlled_document: document_name,
							file,
							reviewer: email("reviewer"),
						},
					})
					.then(({ message: revision }) => {
						const signs = (who, action) =>
							as_call(who, "meddevice_suite.compliance.signing.sign_action", {
								doctype: "Document Revision",
								name: revision.name,
								action,
								password: PASSWORD,
							});
						signs("requester", "Author");
						signs("reviewer", "Review");
						signs("qa", "Approve");
					})
			);
		});
	});

	it("[URS-QMS-009] [URS-QMS-016] the requester sends the change for assessment; an outsider cannot see it", () => {
		cy.window()
			.its("frappe")
			.then(() =>
				cy.call("frappe.client.get_value", {
					doctype: "Controlled Document",
					filters: { name: document_name },
					fieldname: "status",
				})
			)
			.then((r) => expect(r.message.status).to.equal("Effective"));
		cy.then(() =>
			raise("requester", "Clarify the silicone inspection", [
				{
					description: "Issue the new revision",
					action_owner: email("requester"),
					due_date: "today",
				},
				{
					description: "Train the stores team",
					action_owner: email("stores"),
					due_date: "today",
				},
			])
		).then((name) => {
			change = name;
			expect(change).to.match(/^CR-\d{4}-\d{4}$/);
		});

		as("outsider");
		cy.then(() =>
			cy.call("frappe.client.get_count", {
				doctype: "Change Request",
				filters: { name: change },
			})
		).then((r) => expect(r.message).to.equal(0));

		as("requester");
		cy.then(() => open_change());
		cy.window().its("cur_frm.doc.status").should("eq", "Draft");
		cy.window().then(
			(win) =>
				expect(win.cur_frm.get_field("description").df.read_only, "the requester edits").to
					.not.be.ok
		);
		offered("Assess", false);
		button("Send for Assessment").click();
		status_is("Under Assessment");
		cy.contains(FORM, "Waiting for QA to assess its impact").should("be.visible");
	});

	it("[URS-QMS-010] QA records the impact on the form and signs the assessment", () => {
		as("qa");
		cy.then(() => open_change());
		const answers = {
			impact_product_quality: "Yes",
			impact_risk: "No",
			impact_regulatory: "No",
			impact_validation: "No",
			impact_training: "Yes",
			impact_documents: "Yes",
			impact_stock: "No",
			classification: "Major",
		};
		for (const [fieldname, value] of Object.entries(answers)) {
			cy.get(`${FORM} select[data-fieldname="${fieldname}"]`).select(value);
		}
		cy.get(`${FORM} textarea[data-fieldname="impact_summary"]`).type(
			"Inspection procedure and training change; no effect on the device design."
		);
		cy.get(`${FORM} input[data-fieldname="effectiveness_check_days"]`)
			.clear()
			.type("0")
			.blur();
		cy.window().should((win) => {
			const doc = win.cur_frm.doc;
			expect(doc.classification).to.equal("Major");
			expect(doc.effectiveness_check_days).to.equal(0);
			expect(doc.impact_summary).to.contain("no effect");
		});
		cy.get(`${FORM} .primary-action`).contains("Save").click();
		cy.window()
			.its("cur_frm")
			.should((frm) => expect(frm.is_dirty()).to.equal(false));

		sign("Assess", "Sign: Assess");
		status_is("Awaiting Approval");
	});

	it("[URS-QMS-011] QA approves, then Management, each offered only their own approval", () => {
		as("requester");
		cy.then(() => open_change());
		offered("Approve", false);

		as("qa");
		cy.then(() => open_change());
		sign("Approve", "Sign: Approve");
		status_is("Awaiting Management Approval");
		offered("Approve", false); // QA has approved; the second approval is Management's

		as("manager");
		cy.then(() => open_change());
		sign("Approve", "Sign: Approve");
		status_is("Approved");
		cy.contains(FORM, "Carry out the actions: 0 of 2 done").should("be.visible");
		cy.get(`${FORM} .page-head .indicator-pill`).should("contain", "Approved");
	});

	it("[URS-QMS-014] the new revision of the procedure offers the approved change", () => {
		as("requester");
		cy.visit("/desk/document-revision/new");
		cy.window().its("cur_frm.doc.__islocal").should("eq", 1);
		cy.window().then((win) => win.cur_frm.set_value("controlled_document", document_name));
		cy.window()
			.its("cur_frm")
			.should(
				(frm) =>
					expect(
						frm.get_docfield("change_request").reqd,
						"needed for a document in effect"
					).to.be.ok
			);
		cy.get(`${FORM} input[data-fieldname="change_request"]`).click().type(change);
		cy.get('[role="listbox"]:visible [role="option"]').should("contain", change);
	});

	it("[URS-QMS-012] each owner records their action done; the change is then implemented", () => {
		const record = (who, note) => {
			as(who);
			cy.then(() => open_change());
			button("Record Action Done").click();
			dialog_open("Record an action as done");
			type_in_dialog("textarea", "note", note);
			cy.get(".modal:visible .btn-primary").contains("Record as Done").click();
		};

		as("outsider");
		cy.then(() =>
			cy.call("frappe.client.get_count", {
				doctype: "Change Request",
				filters: { name: change },
			})
		).then((r) => expect(r.message).to.equal(0));

		record("stores", "Stores team trained on the new check");
		cy.contains(FORM, "1 of 2 done").should("be.visible");
		offered("Record Action Done", false); // stores' own action is done

		record("requester", "Revision 01 drafted for review");
		status_is("Implemented");
	});

	it("[URS-QMS-013] QA verifies the change was effective and closes it", () => {
		as("requester");
		cy.then(() => open_change());
		offered("Verify: Effective", false);

		as("qa");
		cy.then(() => open_change());
		sign(
			"Verify: Effective",
			"Sign: Verify Effective",
			"No hardness rejects since the change"
		);
		status_is("Closed");
		cy.contains(FORM, "Closed on").should("contain", "Effective");
	});

	it("[URS-QMS-011] [URS-QMS-016] a returned change shows the reason, and the requester may withdraw it", () => {
		cy.then(() =>
			raise("requester", "Shorten the drying time", [
				{
					description: "Revise the drying instruction",
					action_owner: email("requester"),
					due_date: "today",
				},
			])
		).then((name) => {
			cy.call(`${CHANGE_CONTROL}.submit_for_assessment`, { name });
			as("qa");
			open_change(name);
			sign(
				"Return for Changes",
				"Sign: Return",
				"Give the validation data for the shorter time"
			);
			status_is("Draft");

			as("requester");
			open_change(name);
			cy.contains(FORM, "Returned: Give the validation data").should("be.visible");
			cy.get(`${FORM} .inner-group-button[data-label="Actions"] button`).click();
			cy.get(`${FORM} .dropdown-item[data-label="Withdraw"]`).click();
			dialog_open(`Withdraw ${name}`);
			type_in_dialog("textarea", "reason", "The supplier changed the material instead");
			cy.get(".modal:visible .btn-primary").contains("Withdraw").click();
			status_is("Withdrawn");
			cy.contains(FORM, "Withdrawn: The supplier changed the material instead").should(
				"be.visible"
			);
		});
	});
});

// A submittable record type in this app's Compliance module is regulated, as every
// record type this app builds is (meddevice_suite/compliance/audit_scope.py).
const RECORD = "Test MDS Reason Record";
const ROUTE = "/desk/test-mds-reason-record";
const FULL_ACCESS = { read: 1, write: 1, create: 1, submit: 1, cancel: 1, amend: 1 };

function audit_reason(name, action) {
	return cy
		.call("frappe.client.get_list", {
			doctype: "Audit Event",
			filters: { reference_doctype: RECORD, reference_name: name, action },
			fields: ["reason"],
		})
		.then((r) => r.message.map((event) => event.reason));
}

function open_dialog() {
	return cy.get(".modal:visible").last();
}

function give_reason(title, reason) {
	open_dialog().should("contain", title);
	open_dialog().find('[data-fieldname="reason"] textarea').type(reason);
	open_dialog().find(".btn-primary").contains("Continue").click();
}

function submitted_record(title) {
	return cy.insert_doc(RECORD, { title, docstatus: 1 });
}

function setup() {
	cy.login();
	cy.visit("/desk");
	cy.insert_doc(
		"DocType",
		{
			name: RECORD,
			module: "Compliance",
			custom: 1,
			is_submittable: 1,
			fields: [
				{ fieldname: "title", fieldtype: "Data", label: "Title", reqd: 1 },
				{
					fieldname: "remarks",
					fieldtype: "Small Text",
					label: "Remarks",
					allow_on_submit: 1,
				},
			],
			permissions: [{ role: "System Manager", ...FULL_ACCESS }],
		},
		true
	);
}

context("Reason for change", () => {
	before(setup);

	it("[URS-CMP-011] asks for a reason before cancelling and records it", () => {
		submitted_record("Lot UI-001").then((doc) => {
			cy.visit(`${ROUTE}/${doc.name}`);
			cy.get('.page-container:visible button[data-label="Cancel"]').click();
			give_reason(`Reason for cancelling ${doc.name}`, "Damaged in transit");
			open_dialog().should("contain", "Permanently Cancel");
			open_dialog().find(".btn-primary").contains("Yes").click();

			cy.get(".page-container:visible .indicator-pill").should("contain", "Cancelled");
			audit_reason(doc.name, "Cancelled").should("deep.equal", ["Damaged in transit"]);
		});
	});

	it("[URS-CMP-011] leaves the record unchanged when no reason is given", () => {
		submitted_record("Lot UI-002").then((doc) => {
			cy.visit(`${ROUTE}/${doc.name}`);
			cy.get('.page-container:visible button[data-label="Cancel"]').click();
			open_dialog().should("contain", `Reason for cancelling ${doc.name}`);
			// Focus moves into the field once the dialog has finished opening; a close before that is ignored.
			open_dialog().find('[data-fieldname="reason"] textarea').should("have.focus");
			open_dialog().find(".btn-modal-close").click();
			cy.get(".modal:visible").should("not.exist");

			cy.get(".page-container:visible .indicator-pill").should("contain", "Submitted");
			audit_reason(doc.name, "Cancelled").should("deep.equal", []);
		});
	});

	it("[URS-CMP-011] asks for a reason before changing a submitted record", () => {
		submitted_record("Lot UI-003").then((doc) => {
			cy.visit(`${ROUTE}/${doc.name}`);
			cy.get('.page-container:visible [data-fieldname="remarks"] textarea').type(
				"Label reprinted"
			);
			cy.get('.page-container:visible button[data-label="Update"]').click();
			give_reason(`Reason for changing ${doc.name}`, "Label damaged");

			cy.get(".page-container:visible .indicator-pill").should("not.contain", "Not Saved");
			audit_reason(doc.name, "Updated after submission").should("deep.equal", [
				"Label damaged",
			]);
		});
	});

	it("[URS-CMP-011] asks for a reason when an amendment is saved", () => {
		submitted_record("Lot UI-004").then((doc) => {
			cy.call("meddevice_suite.compliance.audit_reason.stage_reason", {
				doctype: RECORD,
				name: doc.name,
				action: "cancel",
				reason: "Wrong lot",
			});
			cy.call("frappe.client.cancel", { doctype: RECORD, name: doc.name });

			cy.visit(`${ROUTE}/${doc.name}`);
			cy.get('.page-container:visible button[data-label="Amend"]').click();
			cy.get('.page-container:visible button[data-label="Save"]').click();
			give_reason(`Reason for amending ${doc.name}`, "Quantity corrected");

			cy.location("pathname").should("not.contain", "/new");
			cy.call("frappe.client.get_value", {
				doctype: RECORD,
				filters: { amended_from: doc.name },
				fieldname: "name",
			}).then((r) =>
				audit_reason(r.message.name, "Amended").should("deep.equal", [
					"Quantity corrected",
				])
			);
		});
	});
});

context("Audit trail review", () => {
	before(setup);

	it("[URS-CMP-012] shows the record's audit trail and opens its full history", () => {
		submitted_record("Lot UI-005").then((doc) => {
			cy.call("meddevice_suite.compliance.audit_reason.stage_reason", {
				doctype: RECORD,
				name: doc.name,
				action: "cancel",
				reason: "Supplier lot rejected",
			});
			cy.call("frappe.client.cancel", { doctype: RECORD, name: doc.name });

			cy.visit(`${ROUTE}/${doc.name}`);
			cy.get(".page-container:visible .timeline-item")
				.should("contain", "Cancelled by")
				.and("contain", "Reason: Supplier lot rejected")
				.and("contain", "Submitted by");

			cy.get(
				'.page-container:visible .inner-group-button[data-label="View"] button'
			).click();
			cy.get('.page-container:visible .dropdown-item[data-label="Audit%20Trail"]').click();
			cy.location("pathname").should("eq", "/desk/audit-event");
			cy.get(".page-container:visible .list-row-container").should("have.length", 2);
		});
	});

	it("[URS-CMP-010] verifies the record's audit trail and signatures on request", () => {
		submitted_record("Lot UI-006").then((doc) => {
			cy.visit(`${ROUTE}/${doc.name}`);
			cy.get(
				'.page-container:visible .inner-group-button[data-label="View"] button'
			).click();
			cy.get(
				'.page-container:visible .dropdown-item[data-label="Verify%20Integrity"]'
			).click();
			cy.get(".modal:visible .modal-title").should("contain", "Integrity verified");
			cy.get(".modal:visible .modal-body").should("contain", "are intact");
		});
	});
});

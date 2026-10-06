// A record archive is built by a background worker and verified from its form (module 54).
function today() {
	return new Date().toISOString().slice(0, 10);
}

function wait_until_completed(name, attempts = 30) {
	return cy
		.call("frappe.client.get_value", {
			doctype: "Record Archive",
			filters: { name },
			fieldname: "status",
		})
		.then((r) => {
			const status = r.message.status;
			expect(status, "archive status").not.to.equal("Failed");
			if (status === "Completed") return;
			expect(attempts, "attempts left").to.be.greaterThan(0);
			cy.wait(1000);
			wait_until_completed(name, attempts - 1);
		});
}

context("Record archive", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
	});

	it("[URS-CMP-025] [URS-CMP-026] builds an archive in the background and verifies it", () => {
		cy.insert_doc("Record Archive", { from_date: today(), to_date: today() }).then(
			(archive) => {
				wait_until_completed(archive.name);

				cy.visit(`/desk/record-archive/${archive.name}`);
				cy.get(".page-container:visible .indicator-pill").should("contain", "Completed");
				cy.get('.page-container:visible [data-fieldname="archive_hash"]').should(
					"not.be.empty"
				);
				cy.get('.page-container:visible button[data-label="Verify"]').click();

				cy.get(".modal:visible").last().should("contain", "Archive verified");
			}
		);
	});
});

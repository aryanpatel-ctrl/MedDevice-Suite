// Mirrors DESKTOP_ICON_VISIBILITY in meddevice_suite/setup/desk.py.
const SHOWN = ["CRM", "Stock", "Buying", "Selling", "Manufacturing", "Quality", "Assets"];
const HIDDEN = [
	"Projects",
	"Website",
	"Budget",
	"Share Management",
	"Subscription",
	"My Workspaces",
];

context("Desk scope", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.get(".desktop-icon").should("have.length.greaterThan", 0);
	});

	it("[URS-PLT-001] shows the areas the client requirement uses", () => {
		SHOWN.forEach((label) => {
			cy.get(`.desktop-icon[data-id="${label}"]`).should("be.visible");
		});
	});

	it("[URS-PLT-001] hides every out-of-scope area, including inside folders", () => {
		cy.window().then((win) => {
			const page = win.frappe.pages["desktop"].desktop_page;
			const rendered = page.apps_icons.flatMap((icon) => [icon, ...icon.child_icons]);
			const rendered_labels = rendered.map((icon) => icon.label);

			HIDDEN.forEach((label) => {
				expect(rendered_labels, label).not.to.include(label);
				cy.get(`.desktop-icon[data-id="${label}"]`).should("not.exist");
			});
		});
	});
});

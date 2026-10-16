"""What a signature binds on each record type whose module declares no content of its own (21 CFR 11.70).

Before fingerprint version 2 (independent review 2026-10-15) a signature on these record types bound
the whole record less what the system maintains (esign.SYSTEM_MAINTAINED), so a field added to the
record type later changed the content of every signature taken before it, and they stopped verifying.
Each record type now lists the fields its signers approve: the ones version 1 bound, written out, less
the record's workflow status, which is the system's (the signature's act and meaning record the
decision). A field added to the record type later is not bound until it is added here; added to a
declaration, it is bound only once it holds a value, so a signature that verified keeps verifying.

Not bound, as in version 1: the record's name (the signature binds the name at signing), who made it
and when (the audit trail holds that), and the bookkeeping of the system after the signature.
"""

from meddevice_suite.compliance.esign import SignedFields

# Module 30, document control
DOCUMENT_REVISION = SignedFields(
	(
		"controlled_document",
		"document_title",
		"document_type",
		"revision",
		"file",
		"file_sha256",
		"summary_of_change",
		"change_request",
		"author",
		"reviewer",
		"approver",
		"effective_date",
		"amended_from",
	)
)
# The periodic review moves only the next review date and the alert's bookkeeping, which are left out.
CONTROLLED_DOCUMENT = SignedFields(
	("title", "document_type", "department", "process_owner", "review_period_years", "withdrawn_reason")
)

# Module 36, change control: the plan as assessed and approved; not what is recorded as it is carried out.
CHANGE_REQUEST = SignedFields(
	(
		"title",
		"change_type",
		"description",
		"reason",
		"classification",
		"requested_by",
		"requested_on",
		"decision_reason",
		"impact_product_quality",
		"impact_risk",
		"impact_regulatory",
		"impact_validation",
		"impact_training",
		"impact_documents",
		"impact_stock",
		"effectiveness_check_days",
		"impact_summary",
		"assessed_by",
		"assessed_on",
		"qa_approver",
		"management_approver",
		"approved_on",
		"effectiveness_outcome",
		"effectiveness_note",
		"closed_on",
		"amended_from",
	),
	{
		"affected_records": ("reference_doctype", "reference_name", "note"),
		"actions": ("description", "action_owner", "due_date", "target_doctype", "target_name"),
	},
)

# Modules 16 and 17: the decision on a lot; not the stock moves it causes.
LOT_RELEASE = SignedFields(
	(
		"batch",
		"item",
		"item_name",
		"lot_status_before",
		"quantity",
		"source_doctype",
		"source_name",
		"supplier",
		"held_since",
		"decision",
		"quality_inspection",
		"reason",
		"decided_by",
		"decided_at",
		"amended_from",
	)
)

# Module 4: the decision on a supplier; not the certificate alert's bookkeeping.
SUPPLIER_APPROVAL = SignedFields(
	(
		"supplier",
		"supplier_name",
		"valid_until",
		"certificate_number",
		"certificate_expiry",
		"certificate",
		"decision",
		"reason",
		"decided_by",
		"decided_at",
		"amended_from",
	),
	{"items": ("item_code", "item_name")},
)

# Module 5: the licence as verified; not the expiry alert's bookkeeping.
CUSTOMER_LICENCE = SignedFields(
	(
		"customer",
		"customer_name",
		"licence_type",
		"licence_number",
		"issuing_authority",
		"valid_from",
		"valid_until",
		"licence_copy",
		"reason",
		"decided_by",
		"decided_at",
		"amended_from",
	)
)

# Module 59: the task and its result as approved or returned.
WORK_TASK = SignedFields(
	(
		"subject",
		"due_date",
		"assigned_to",
		"approver",
		"description",
		"reference_doctype",
		"reference_name",
		"result",
		"submitted_on",
		"return_reason",
		"approved_by",
		"approved_on",
		"withdrawn_reason",
		"amended_from",
	)
)

# Module 38: the calibration as performed and the products it may affect; not QA's later assessment,
# which QA signs with its own signature.
CALIBRATION_RECORD = SignedFields(
	(
		"asset",
		"performed_on",
		"calibrated_by",
		"procedure",
		"laboratory",
		"certificate_number",
		"certificate",
		"result",
		"out_of_tolerance",
		"interval_days",
		"next_due_date",
		"completed_by",
		"last_good_calibration",
		"amended_from",
	),
	{
		"standards": ("standard", "standard_due_on"),
		"points": (
			"parameter",
			"unit",
			"nominal",
			"lower_limit",
			"upper_limit",
			"as_found",
			"as_left",
			"found_in_tolerance",
			"left_in_tolerance",
		),
		"impact": ("reference_doctype", "reference_name", "date", "item_code", "batch_no", "workstation"),
	},
)
MAINTENANCE_RECORD = SignedFields(
	(
		"asset",
		"performed_on",
		"procedure",
		"actions_performed",
		"downtime_hours",
		"result",
		"interval_days",
		"next_due_date",
		"completed_by",
		"amended_from",
	),
	{"checks": ("check", "outcome", "remark")},
)

# Module 40: the qualification as executed; who executed and approved it are the signatures' own.
EQUIPMENT_QUALIFICATION = SignedFields(
	("asset", "stage", "occasion", "protocol", "performed_on", "outcome", "amended_from"),
	{"tests": ("test", "acceptance_criteria", "observed", "outcome")},
)

# Module 37, training: what was planned and signed for; not the outcome later signatures record.
TRAINING_MATRIX = SignedFields(
	("designation", "approved_by", "approved_at", "amended_from"),
	{"items": ("controlled_document", "document_title", "read_and_understand_allowed", "validity_months")},
)
TRAINING_RECORD = SignedFields(
	(
		"employee",
		"employee_name",
		"designation",
		"trainee_user",
		"controlled_document",
		"document_revision",
		"revision",
		"trigger",
		"due_date",
		"training_method",
		"training_date",
		"trainer",
		"read_and_understand_allowed",
		"assessor",
		"assessment_method",
		"certificate",
		"validity_months",
		"amended_from",
	)
)

# Module 39, risk management
RISK_SCALE = ("level", "level_name", "description")
RISK_ACCEPTABILITY_POLICY = SignedFields(
	("title", "policy_statement", "reviewed_by", "reviewed_at", "amended_from"),
	{
		"severity_levels": RISK_SCALE,
		"probability_levels": RISK_SCALE,
		"matrix": ("severity", "probability", "acceptability"),
	},
)
RISK_ASSESSMENT = SignedFields(
	(
		"risk_management_file",
		"family_name",
		"revision",
		"policy",
		"change_request",
		"risk_review",
		"summary_of_change",
		"author",
		"reviewer",
		"approver",
		"approved_on",
		"intended_use",
		"foreseeable_misuse",
		"risk_management_plan",
		"highest_residual_risk",
		"amended_from",
	),
	{
		"items": ("item", "item_name"),
		"hazards": (
			"hazard_id",
			"hazard_category",
			"hazard",
			"sequence_of_events",
			"hazardous_situation",
			"harm",
			"severity",
			"probability",
			"initial_acceptability",
			"residual_severity",
			"residual_probability",
			"residual_acceptability",
			"justification",
		),
		"controls": (
			"hazard_id",
			"option",
			"description",
			"control_doctype",
			"control_name",
			"pinned_version",
			"implementation_evidence_type",
			"implementation_evidence",
			"effectiveness_evidence_type",
			"effectiveness_evidence",
			"introduces_hazard_id",
		),
	},
)
RISK_MANAGEMENT_REPORT = SignedFields(
	(
		"risk_assessment",
		"risk_management_file",
		"family_name",
		"plan_implemented",
		"plan_review",
		"overall_residual_risk",
		"overall_conclusion",
		"author",
		"approver",
		"approved_on",
		"amended_from",
	),
	{"information_sources": ("controlled_document", "document_title")},
)
RISK_REVIEW = SignedFields(
	(
		"risk_management_file",
		"family_name",
		"trigger",
		"source_doctype",
		"source_name",
		"source_state",
		"details",
		"raised_on",
		"due_date",
		"outcome",
		"rationale",
		"resulting_revision",
		"closed_by",
		"closed_on",
		"amended_from",
	)
)

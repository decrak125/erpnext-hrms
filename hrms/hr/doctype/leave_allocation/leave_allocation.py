# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, date_diff, flt, formatdate, get_link_to_form, getdate

from hrms.hr.doctype.leave_application.leave_application import get_approved_leaves_for_period
from hrms.hr.doctype.leave_ledger_entry.leave_ledger_entry import (
	create_leave_ledger_entry,
	expire_allocation,
	process_expired_allocation,
)
from hrms.hr.utils import create_additional_leave_ledger_entry, get_leave_period, set_employee_name
from hrms.hr.utils import get_monthly_earned_leave as _get_monthly_earned_leave


class OverlapError(frappe.ValidationError):
	pass


class BackDatedAllocationError(frappe.ValidationError):
	pass


class OverAllocationError(frappe.ValidationError):
	pass


class LessAllocationError(frappe.ValidationError):
	pass


class ValueMultiplierError(frappe.ValidationError):
	pass


class LeaveAllocation(Document):
	def validate(self):
		self.validate_period()
		self.validate_allocation_overlap()
		self.validate_lwp()
		set_employee_name(self)
		self.set_total_leaves_allocated()
		self.validate_leave_days_and_dates()

	def validate_leave_days_and_dates(self):
		# all validations that should run on save as well as on update after submit
		self.validate_back_dated_allocation()
		self.validate_total_leaves_allocated()
		self.validate_leave_allocation_days()

	def validate_leave_allocation_days(self):
		company = frappe.db.get_value("Employee", self.employee, "company")
		leave_period = get_leave_period(self.from_date, self.to_date, company)
		max_leaves_allowed = frappe.db.get_value("Leave Type", self.leave_type, "max_leaves_allowed")

		if max_leaves_allowed > 0:
			leave_allocated = 0
			if leave_period:
				leave_allocated = get_leave_allocation_for_period(
					self.employee,
					self.leave_type,
					leave_period[0].from_date,
					leave_period[0].to_date,
					exclude_allocation=self.name,
				)
			leave_allocated += flt(self.new_leaves_allocated)
			if leave_allocated > max_leaves_allowed:
				frappe.throw(
					_(
						"Total allocated leaves are more than maximum allocation allowed for {0} leave type for employee {1} in the period"
					).format(self.leave_type, self.employee),
					OverAllocationError,
				)

	def on_submit(self):
		self.create_leave_ledger_entry()

		# expire all unused leaves in the ledger on creation of carry forward allocation
		allocation = get_previous_allocation(self.from_date, self.leave_type, self.employee)
		if self.carry_forward and allocation:
			expire_allocation(allocation)

	def on_cancel(self):
		self.create_leave_ledger_entry(submit=False)
		if self.leave_policy_assignment:
			self.update_leave_policy_assignments_when_no_allocations_left()
		if self.carry_forward:
			self.set_carry_forwarded_leaves_in_previous_allocation(on_cancel=True)

	# nosemgrep: frappe-semgrep-rules.rules.frappe-modifying-but-not-comitting
	def on_update_after_submit(self):
		if self.has_value_changed("new_leaves_allocated"):
			self.validate_earned_leave_update()
			self.validate_against_leave_applications()

			# recalculate total leaves allocated
			self.total_leaves_allocated = flt(self.unused_leaves) + flt(self.new_leaves_allocated)
			# run required validations again since total leaves are being updated
			self.validate_leave_days_and_dates()

			leaves_to_be_added = flt(
				(self.new_leaves_allocated - self.get_existing_leave_count()),
				self.precision("new_leaves_allocated"),
			)

			args = {
				"leaves": leaves_to_be_added,
				"from_date": self.from_date,
				"to_date": self.to_date,
				"is_carry_forward": 0,
			}
			create_leave_ledger_entry(self, args, True)
			self.db_update()

	def get_existing_leave_count(self):
		ledger_entries = frappe.get_all(
			"Leave Ledger Entry",
			filters={
				"transaction_type": "Leave Allocation",
				"transaction_name": self.name,
				"employee": self.employee,
				"company": self.company,
				"leave_type": self.leave_type,
				"is_carry_forward": 0,
				"docstatus": 1,
			},
			fields=["SUM(leaves) as total_leaves"],
		)

		return ledger_entries[0].total_leaves if ledger_entries else 0

	def validate_earned_leave_update(self):
		if self.leave_policy_assignment and frappe.db.get_value(
			"Leave Type", self.leave_type, "is_earned_leave"
		):
			msg = _("Cannot update allocation for {0} after submission").format(
				frappe.bold(_("Earned Leaves"))
			)
			msg += "<br><br>"
			msg += _(
				"Earned Leaves are auto-allocated via scheduler based on the annual allocation set in the Leave Policy: {0}"
			).format(get_link_to_form("Leave Policy", self.leave_policy))
			frappe.throw(msg, title=_("Not Allowed"))

	def validate_against_leave_applications(self):
		leaves_taken = get_approved_leaves_for_period(
			self.employee, self.leave_type, self.from_date, self.to_date
		)
		if flt(leaves_taken) > flt(self.total_leaves_allocated):
			if frappe.db.get_value("Leave Type", self.leave_type, "allow_negative"):
				frappe.msgprint(
					_(
						"Note: Total allocated leaves {0} shouldn't be less than already approved leaves {1} for the period"
					).format(self.total_leaves_allocated, leaves_taken)
				)
			else:
				frappe.throw(
					_(
						"Total allocated leaves {0} cannot be less than already approved leaves {1} for the period"
					).format(self.total_leaves_allocated, leaves_taken),
					LessAllocationError,
				)

	def update_leave_policy_assignments_when_no_allocations_left(self):
		allocations = frappe.db.get_list(
			"Leave Allocation",
			filters={"docstatus": 1, "leave_policy_assignment": self.leave_policy_assignment},
		)
		if len(allocations) == 0:
			frappe.db.set_value(
				"Leave Policy Assignment", self.leave_policy_assignment, "leaves_allocated", 0
			)

	def validate_period(self):
		if date_diff(self.to_date, self.from_date) <= 0:
			frappe.throw(_("To date cannot be before from date"))

	def validate_lwp(self):
		if frappe.db.get_value("Leave Type", self.leave_type, "is_lwp"):
			frappe.throw(
				_("Leave Type {0} cannot be allocated since it is leave without pay").format(self.leave_type)
			)

	def validate_allocation_overlap(self):
		leave_allocation = frappe.db.sql(
			"""
			SELECT
				name
			FROM `tabLeave Allocation`
			WHERE
				employee=%s AND leave_type=%s
				AND name <> %s AND docstatus=1
				AND to_date >= %s AND from_date <= %s""",
			(self.employee, self.leave_type, self.name, self.from_date, self.to_date),
		)

		if leave_allocation:
			frappe.msgprint(
				_("{0} already allocated for Employee {1} for period {2} to {3}").format(
					self.leave_type, self.employee, formatdate(self.from_date), formatdate(self.to_date)
				)
			)

			frappe.throw(
				_("Reference")
				+ f': <a href="/app/Form/Leave Allocation/{leave_allocation[0][0]}">{leave_allocation[0][0]}</a>',
				OverlapError,
			)

	def validate_back_dated_allocation(self):
		future_allocation = frappe.db.sql(
			"""select name, from_date from `tabLeave Allocation`
			where employee=%s and leave_type=%s and docstatus=1 and from_date > %s
			and carry_forward=1""",
			(self.employee, self.leave_type, self.to_date),
			as_dict=1,
		)

		if future_allocation:
			frappe.throw(
				_(
					"Leave cannot be allocated before {0}, as leave balance has already been carry-forwarded in the future leave allocation record {1}"
				).format(formatdate(future_allocation[0].from_date), future_allocation[0].name),
				BackDatedAllocationError,
			)

	@frappe.whitelist()
	def set_total_leaves_allocated(self):
		self.unused_leaves = flt(
			get_carry_forwarded_leaves(self.employee, self.leave_type, self.from_date, self.carry_forward),
			self.precision("unused_leaves"),
		)

		self.total_leaves_allocated = flt(
			flt(self.unused_leaves) + flt(self.new_leaves_allocated),
			self.precision("total_leaves_allocated"),
		)

		self.limit_carry_forward_based_on_max_allowed_leaves()

		if self.carry_forward:
			self.set_carry_forwarded_leaves_in_previous_allocation()

		if (
			not self.total_leaves_allocated
			and not frappe.db.get_value("Leave Type", self.leave_type, "is_earned_leave")
			and not frappe.db.get_value("Leave Type", self.leave_type, "is_compensatory")
		):
			frappe.throw(_("Total leaves allocated is mandatory for Leave Type {0}").format(self.leave_type))

	def limit_carry_forward_based_on_max_allowed_leaves(self):
		max_leaves_allowed = frappe.db.get_value("Leave Type", self.leave_type, "max_leaves_allowed")
		if max_leaves_allowed and self.total_leaves_allocated > max_leaves_allowed:
			self.total_leaves_allocated = max_leaves_allowed
			self.unused_leaves = max_leaves_allowed - flt(self.new_leaves_allocated)

	def set_carry_forwarded_leaves_in_previous_allocation(self, on_cancel=False):
		"""Set carry forwarded leaves in previous allocation"""
		previous_allocation = get_previous_allocation(self.from_date, self.leave_type, self.employee)
		if on_cancel:
			self.unused_leaves = 0.0
		if previous_allocation:
			frappe.db.set_value(
				"Leave Allocation",
				previous_allocation.name,
				"carry_forwarded_leaves_count",
				self.unused_leaves,
			)

	def validate_total_leaves_allocated(self):
		# Adding a day to include To Date in the difference
		date_difference = date_diff(self.to_date, self.from_date) + 1
		if date_difference < self.total_leaves_allocated:
			if frappe.db.get_value("Leave Type", self.leave_type, "allow_over_allocation"):
				frappe.msgprint(
					_(
						"<b>Total Leaves Allocated</b> are more than the number of days in the allocation period"
					),
					indicator="orange",
					alert=True,
				)
			else:
				frappe.throw(
					_(
						"<b>Total Leaves Allocated</b> are more than the number of days in the allocation period"
					),
					exc=OverAllocationError,
					title=_("Over Allocation"),
				)

	def create_leave_ledger_entry(self, submit=True):
		if self.unused_leaves:
			expiry_days = frappe.db.get_value(
				"Leave Type", self.leave_type, "expire_carry_forwarded_leaves_after_days"
			)
			end_date = add_days(self.from_date, expiry_days - 1) if expiry_days else self.to_date
			args = dict(
				leaves=self.unused_leaves,
				from_date=self.from_date,
				to_date=min(getdate(end_date), getdate(self.to_date)),
				is_carry_forward=1,
			)
			create_leave_ledger_entry(self, args, submit)
			if submit and getdate(end_date) < getdate():
				show_expire_leave_dialog(self.unused_leaves, self.leave_type)

		args = dict(
			leaves=self.new_leaves_allocated,
			from_date=self.from_date,
			to_date=self.to_date,
			is_carry_forward=0,
		)
		create_leave_ledger_entry(self, args, submit)

	@frappe.whitelist()
	def allocate_leaves_manually(self, new_leaves, from_date=None):
		if from_date and not (getdate(self.from_date) <= getdate(from_date) <= getdate(self.to_date)):
			frappe.throw(
				_("Cannot allocate leaves outside the allocation period {0} - {1}").format(
					frappe.bold(formatdate(self.from_date)), frappe.bold(formatdate(self.to_date))
				),
				title=_("Invalid Dates"),
			)

		new_allocation = flt(self.total_leaves_allocated) + flt(new_leaves)
		new_allocation_without_cf = flt(
			flt(self.get_existing_leave_count()) + flt(new_leaves),
			self.precision("total_leaves_allocated"),
		)

		max_leaves_allowed = frappe.db.get_value("Leave Type", self.leave_type, "max_leaves_allowed")
		if new_allocation > max_leaves_allowed and max_leaves_allowed > 0:
			new_allocation = max_leaves_allowed

		annual_allocation = frappe.db.get_value(
			"Leave Policy Detail",
			{"parent": self.leave_policy, "leave_type": self.leave_type},
			"annual_allocation",
		)
		annual_allocation = flt(annual_allocation, self.precision("total_leaves_allocated"))

		if (
			new_allocation != self.total_leaves_allocated
			# annual allocation as per policy should not be exceeded
			and new_allocation_without_cf <= annual_allocation
		):
			self.db_set("total_leaves_allocated", new_allocation, update_modified=False)

			date = from_date or frappe.flags.current_date or getdate()
			create_additional_leave_ledger_entry(self, new_leaves, date)

			text = _("{0} leaves were manually allocated by {1} on {2}").format(
				frappe.bold(new_leaves), frappe.session.user, frappe.bold(formatdate(date))
			)
			self.add_comment(comment_type="Info", text=text)
			frappe.msgprint(
				_("{0} leaves allocated successfully").format(frappe.bold(new_leaves)),
				indicator="green",
				alert=True,
			)

		else:
			msg = _("Total leaves allocated cannot exceed annual allocation of {0}.").format(
				frappe.bold(_(annual_allocation))
			)
			msg += "<br><br>"
			msg += _("Reference: {0}").format(get_link_to_form("Leave Policy", self.leave_policy))
			frappe.throw(msg, title=_("Annual Allocation Exceeded"))

	@frappe.whitelist()
	def get_monthly_earned_leave(self):
		doj = frappe.db.get_value("Employee", self.employee, "date_of_joining")

		annual_allocation = frappe.db.get_value(
			"Leave Policy Detail",
			{
				"parent": self.leave_policy,
				"leave_type": self.leave_type,
			},
			"annual_allocation",
		)

		frequency, rounding = frappe.db.get_value(
			"Leave Type",
			self.leave_type,
			[
				"earned_leave_frequency",
				"rounding",
			],
		)

		return _get_monthly_earned_leave(doj, annual_allocation, frequency, rounding)


def get_previous_allocation(from_date, leave_type, employee):
	"""Returns document properties of previous allocation"""
	Allocation = frappe.qb.DocType("Leave Allocation")
	allocations = (
		frappe.qb.from_(Allocation)
		.select(
			Allocation.name,
			Allocation.from_date,
			Allocation.to_date,
			Allocation.employee,
			Allocation.leave_type,
		)
		.where(
			(Allocation.employee == employee)
			& (Allocation.leave_type == leave_type)
			& (Allocation.to_date < from_date)
			& (Allocation.docstatus == 1)
		)
		.orderby(Allocation.to_date, order=frappe.qb.desc)
		.limit(1)
	).run(as_dict=True)

	return allocations[0] if allocations else None


def get_leave_allocation_for_period(employee, leave_type, from_date, to_date, exclude_allocation=None):
	from frappe.query_builder.functions import Sum

	Allocation = frappe.qb.DocType("Leave Allocation")
	return (
		frappe.qb.from_(Allocation)
		.select(Sum(Allocation.total_leaves_allocated).as_("total_allocated_leaves"))
		.where(
			(Allocation.employee == employee)
			& (Allocation.leave_type == leave_type)
			& (Allocation.docstatus == 1)
			& (Allocation.name != exclude_allocation)
			& (
				(Allocation.from_date.between(from_date, to_date))
				| (Allocation.to_date.between(from_date, to_date))
				| ((Allocation.from_date < from_date) & (Allocation.to_date > to_date))
			)
		)
	).run()[0][0] or 0.0


def get_carry_forwarded_leaves(employee, leave_type, date, carry_forward=None):
	"""Returns carry forwarded leaves for the given employee"""
	unused_leaves = 0.0
	previous_allocation = get_previous_allocation(date, leave_type, employee)
	if carry_forward and previous_allocation:
		validate_carry_forward(leave_type)
		unused_leaves = get_unused_leaves(
			employee, leave_type, previous_allocation.from_date, previous_allocation.to_date
		)
		if unused_leaves:
			max_carry_forwarded_leaves = frappe.db.get_value(
				"Leave Type", leave_type, "maximum_carry_forwarded_leaves"
			)
			if max_carry_forwarded_leaves and unused_leaves > flt(max_carry_forwarded_leaves):
				unused_leaves = flt(max_carry_forwarded_leaves)

	return unused_leaves


def get_unused_leaves(employee, leave_type, from_date, to_date):
	"""Returns unused leaves between the given period while skipping leave allocation expiry"""
	leaves = frappe.get_all(
		"Leave Ledger Entry",
		filters={
			"employee": employee,
			"leave_type": leave_type,
			"from_date": (">=", from_date),
			"to_date": ("<=", to_date),
		},
		or_filters={"is_expired": 0, "is_carry_forward": 1},
		fields=["sum(leaves) as leaves"],
	)
	return flt(leaves[0]["leaves"])


def validate_carry_forward(leave_type):
	if not frappe.db.get_value("Leave Type", leave_type, "is_carry_forward"):
		frappe.throw(_("Leave Type {0} cannot be carry-forwarded").format(leave_type))


def show_expire_leave_dialog(expired_leaves, leave_type):
	frappe.msgprint(
		title=_("Leaves Expired"),
		msg=_(
			"{0} leaves from allocation for {1} leave type have expired and will be processed during the next scheduled job. It is recommended to expire them now before creating new leave policy assignments."
		).format(frappe.bold(expired_leaves), frappe.bold(leave_type)),
		indicator="orange",
		primary_action={
			"label": _("Expire Leaves"),
			"server_action": "hrms.hr.doctype.leave_allocation.leave_allocation.expire_carried_forward_allocation",
			"hide_on_success": True,
		},
	)


@frappe.whitelist()
def expire_carried_forward_allocation():
	if frappe.has_permission(doctype="Leave Allocation", ptype="submit", user=frappe.session.user):
		process_expired_allocation()
	else:
		frappe.throw(_("You do not have permission to complete this action"), frappe.PermissionError)

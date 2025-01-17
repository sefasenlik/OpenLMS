from markupsafe import Markup
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError, AccessError

class Proposal(models.Model):
	_name = 'student.proposal'
	_description = 'PaLMS - Project Proposal'
	_inherit = ['mail.thread', 'student.utils']


	name = fields.Char('Proposal Name', required=True, translate=True)
		
	@api.depends('proponent')
	def _compute_student_details(self):
		self.email = self.proponent.student_email
		self.phone = self.proponent.student_phone
		self.student_program = self.proponent.student_program.name
		self.student_degree = self.proponent.progress
		self.student_id = self.proponent.student_id

	# Assigns the student account created for this user
	def _default_proponent(self):
		student = self.env['student.student'].sudo().search([('student_account.id', '=', self.env.uid)], limit=1)
		self._compute_student_details()
		if student:
			return student.id 
		else:
			raise ValidationError("Student account could not be found. Please contact the supervisor.")
		
	@api.depends('proponent')
	def _compute_proponent_details(self):
		self.proponent_account = self.proponent.student_account
		self.proponent_faculty = self.proponent.student_faculty
		
		# ♥ It doesn't work. How to filter professors based on student faculty?
		return {'domain': {'proposal_professor': [('professor_faculty.id','=',self.proponent_faculty.id)]}}
	
	proponent = fields.Many2one('student.student', string='Proposed by', default=_default_proponent, readonly=True, required=True)
	proponent_account= fields.Many2one('res.users', string="Proposing Account", compute='_compute_proponent_details', store=True)
	proponent_faculty = fields.Many2one('student.faculty', string="Proposing Student Faculty", compute='_compute_proponent_details', store=True)

	proposal_professor = fields.Many2one('student.professor', string='Professor', required=True)
	professor_account= fields.Many2one('res.users', string="Professor Account", compute='_compute_professor_account', store=True)

	@api.depends('proposal_professor')
	def _compute_professor_account(self):
		for proposal in self:
			proposal.professor_account = proposal.proposal_professor.professor_account

	email = fields.Char('Email', compute="_compute_student_details", store=True, readonly=True)
	additional_email = fields.Char('Additional Email', required=False)
	phone = fields.Char('Phone', compute="_compute_student_details", store=True, readonly=True)
	additional_phone = fields.Char('Additional Phone', required=False)
	telegram = fields.Char('Telegram ID', required=False)
	student_program = fields.Char("Student Track", compute="_compute_student_details", store=True, readonly=True)
	student_degree = fields.Char("Academic Year", compute="_compute_student_details", store=True, readonly=True)
	student_id = fields.Char("Student ID", compute="_compute_student_details", store=True, readonly=True)
            
	project_id = fields.Many2one('student.project', string="Converted to", readonly=True)
    
	type = fields.Selection([('cw', 'Course Work (Курсовая работа)'), ('fqw', 'Final Qualifying Work (ВКР)')], string="Proposal Project Type", required=True)
	format = fields.Selection([('research', 'Research'), ('project', 'Project'), ('startup', 'Start-up')], string="Format", required=True)
	language = fields.Selection([('en', 'English'), ('ru', 'Russian')], default="en", string="Language", required=True)

	description = fields.Text('Detailed Description', required=True, translate=True)
	results = fields.Text('Expected Results', translate=True)
	feedback = fields.Text('Professor Feedback', translate=True)

	additional_files = fields.Many2many(comodel_name="ir.attachment", string="Additional Files") 

	state = fields.Selection([('draft', 'Draft'),('sent', 'Sent'),('accepted', 'Accepted'),('confirmed', 'Confirmed'),('rejected', 'Rejected')], default='draft', readonly=True, string='Proposal State', store=True)
	sent_date = fields.Date(string='Sent Date')    
	
	@api.constrains("feedback")
	def _check_reason_modified(self):
		if not self.env.user.has_group("student.group_professor"):
			raise UserError("Only professors can modify the feedback!")
        
		if self.env.user.id != self.professor_account.id:
			raise UserError("This project proposal is not sent to you.")

	@api.onchange('description', 'results', 'proposal_professor', 'additional_email', 'additional_phone', 'telegram')
	def _check_user_identity(self):
		if not self.env.user.has_group('student.group_supervisor'):
			if self.proponent_account != self.env.user:
				raise AccessError("You can only modify proposals that you created. If you require assistance, contact the supervisor.")

	@api.depends('project_id.state')
	def action_view_proposal_send(self):
		self._check_user_identity()

		if self.state != 'draft':
			raise UserError("The proposal is already sent!")
		else:
			self.write({'state': 'sent'})

			# Updates the ownership of files for other users to access them
			for attachment in self.additional_files:
				attachment.write({'res_model': self._name, 'res_id': self.id})
            
			# Send the email --------------------
			subtype_id = self.env.ref('student.student_message_subtype_email')
			template = self.env.ref('student.email_template_proposal_send')
			template.send_mail(self.id, email_values={'subtype_id': subtype_id.id}, force_send=True)
			# -----------------------------------

			# Construct the message that is to be sent to the user
			message_text = f'<strong>Project Proposal Received</strong><p> ' + self.proponent_account.name + " sent a project proposal «" + self.name + "». Please evaluate the proposal.</p>"

			# Use the send_message utility function to send the message
			self.env['student.utils'].send_message('proposal', Markup(message_text), self.professor_account, self.proponent_account, (str(self.id),str(self.name)))

			self.sent_date = fields.Date.today()

			return self.env['student.utils'].message_display('Sent', 'The project proposal is submitted for review.', False)

	def action_view_proposal_cancel(self):
		self._check_user_identity()

		if self.state == 'sent':
			self.write({'state': 'draft'})

			return self.env['student.utils'].message_display('Cancellation', 'The proposal submission is cancelled.', False)
		else:
			raise UserError("The proposal is already processed!")
			
	def _check_professor_identity(self):
		if not self.env.user.has_group('student.group_supervisor'):
			if self.professor_account != self.env.user:
				raise AccessError("You can only respond to the proposals sent to you.")

	def action_view_proposal_accept(self):
		self._check_professor_identity()

		if self.proponent.current_project:
			raise ValidationError("This student is already assigned to another project.")

		if self.state == 'sent':
			self.project_id = self.env['student.project'].sudo().create({
				'name': self.name,
				'description': self.description,
				'requirements': 'Not applicable for proposed projects...',
				'results': self.results,
				'campus_id': self.proponent.student_faculty.campus,
				'faculty_id': self.proponent.student_faculty,
				'program_ids': self.proponent.student_program,
				'format': self.format,
				'language': self.language,
				'proposal_id': self.id,
				'professor_id': self.proposal_professor.id,
				'professor_account': self.professor_account.id,
				'assigned': True,
				'student_elected': self.proponent
			})

			project_availability = self.env['student.availability'].create({
				'project_id': self.project_id.id,
				'state': 'waiting',
				'program_id': self.proponent.student_program.id,
				'type': self.type,
				'degree_ids': self.proponent.degree
			})

			self.project_id.write({'availability_ids': project_availability})
			
			self.write({'state': 'accepted'})
            
			# Send the email --------------------
			subtype_id = self.env.ref('student.student_message_subtype_email')
			template = self.env.ref('student.email_template_proposal_accept')
			template.send_mail(self.id, email_values={'subtype_id': subtype_id.id}, force_send=True)
			# -----------------------------------

			# Construct the message that is to be sent to the user
			message_text = f"<strong>Proposal Accepted</strong><p> The proposal is accepted by <i>" + self.professor_account.name + "</i> and converted to a project submission. It can be assigned to the student after supervisor's approval.</p>"

			# Use the send_message utility function to send the message
			self.env['student.utils'].send_message('proposal', Markup(message_text), self.proponent_account, self.professor_account, (str(self.id),str(self.name)))
			
			return self.env['student.utils'].message_display('Accepted', 'The proposal is accepted and converted to a project.', False)
		else:
			raise UserError("The proposal is already processed or still a draft!")
	
	def _check_feedback(self):
		if not self.feedback:
			raise UserError("You have to provide a reason for rejection.")
		if len(self.feedback) < 20:
			raise UserError("Please provide a more detailed feedback (at least 20 characters).")
		
	def action_view_proposal_reject(self):
		self._check_professor_identity()

		if self.state == 'sent':
			self._check_feedback()

			self.write({'state': 'rejected'})
            
			# Send the email --------------------
			subtype_id = self.env.ref('student.student_message_subtype_email')
			template = self.env.ref('student.email_template_proposal_reject')
			template.send_mail(self.id, email_values={'subtype_id': subtype_id.id}, force_send=True)
			# -----------------------------------

			# Construct the message that is to be sent to the user
			message_text = f'<strong>Proposal Rejected</strong><p> This project proposal is rejected by <i>' + self.professor_account.name + '</i>. Please check the <b>Feedback</b> section to learn about the reason.</p>'

			# Use the send_message utility function to send the message
			self.env['student.utils'].send_message('proposal', Markup(message_text), self.proponent_account, self.professor_account, (str(self.id),str(self.name)))

			return self.env['student.utils'].message_display('Rejection', 'The proposal is rejected.', False)
		else:
			raise UserError("The proposal is already processed or still a draft!")
		
    # RESTRICTIONS #
	@api.constrains('name', 'proposal_professor', 'type', 'format', 'language', 'additional_email', 'additional_phone', 'telegram', 'description', 'results', 'additional_files')
	def _check_initiator_identity(self):
		if self.env.uid != self.proponent_account.id:
			raise ValidationError("Only the creator of the proposal can modify details.")
		
	def unlink(self):
		for record in self:
			if not record.env.user.has_group('student.group_administrator') and record.env.uid != record.proponent_account.id:
				raise UserError(_('Only the proposing student can delete the proposal!'))
		return super(Proposal, self).unlink()
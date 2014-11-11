# -*- coding: utf-8 -*-
##############################################################################
#
#    Author: Nicolas Bessi
#    Copyright 2014 Camptocamp SA
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
import StringIO
import contextlib
import re
from PIL import Image, ImageDraw, ImageFont

from openerp import models, fields, api, _
from openerp.modules import get_module_resource
from openerp import exceptions
from openerp.tools.misc import mod10r


class PaymentSlip(models.Model):
    """From Version 8 payment slip are now a
    new Model related to move line and
    stored in database. This is done because
    with previous implementation changing bank data
    or anything else had as result to modify historical refs.

    Now payment slip is genrated each time a customer invoice is validated
    If you need to alter a payment slip you will have to cancel
    and revalidate the related invoice
    """
    _fill_color = (0, 0, 0, 0)
    _default_font_size = 20
    _compile_get_ref = re.compile('[^0-9]')
    _compile_check_bvr = re.compile('[0-9][0-9]-[0-9]{3,6}-[0-9]')

    _name = 'l10n_ch.payment_slip'

    reference = fields.Char('BVR/ESR Ref.',
                            compute='compute_ref')

    move_line_id = fields.Many2one('account.move.line',
                                   'Related move',
                                   ondelete='cascade')

    amout_total = fields.Float('Total amount of BVR/ESR',
                               compute='compute_amount',
                               store=True)

    scan_line = fields.Char('Scan Line',
                            compute='compute_scan_line',
                            store=True)
    scan_line_div = fields.Char('Scan Line Div',
                                compute='compute_scan_line_div',
                                store=True)

    invoice_id = fields.Many2one('Related invoice',
                                 related='move_line_id.invoice')

    @api.model
    def _can_generate(self, move_line):
        ''' Determine if BVR should be generated or not. '''
        # We check if the type is bvr, if not we return false
        invoice = move_line.invoice
        if not invoice:
            return False
        return (invoice.partner_bank_id and
                invoice.partner_bank_id.state == 'bvr')

    @api.one
    @api.model
    def _get_adherent_number(self):
        move_line = self.move_line_id
        ad_number = ''
        if move_line.invoice.partner_bank_id.bvr_adherent_num:
            ad_number = move_line.invoice.partner_bank_id.bvr_adherent_num
        return ad_number

    @api.one
    def _compute_amount_hook(self):
        """Hook to return the total amount of pyament slip

        :return: total amount of payment slip
        :rtype: float

        """
        return self.move_line_id.debit

    @api.one
    @api.depends('move_line_id',
                 'move_line_id.debit',
                 'move_line_id.credit')
    def compute_amount(self):
        """Return the total amount of pyament slip

        if you need to override please use
        :py:meth:`_compute_amount_hook`

        :return: total amount of payment slip
        :rtype: float

        """
        return self._compute_amount_hook()

    @api.one
    @api.depends('move_line_id')
    def compute_ref(self):
        """Retrieve ESR/BVR reference from move line in order to print it

        Returns False when no BVR reference should be generated.  No
        reference is generated when a transaction reference already
        exists for the line (likely been generated by a payment service).
        """
        if not self.can_generate(self.move_line_id):
            return ''
        move_line = self.move_line_id
        # We sould not use technical id but will keep it for historical reason
        move_number = str(move_line.id)
        ad_number = self._get_adherent_number()
        if move_line.invoice.number:
            compound = move_line.invoice.number + str(move_line.id)
            move_number = self._compile_get_ref.sub('', compound)
        reference = mod10r(
            ad_number + move_number.rjust(26 - len(ad_number), '0')
        )
        self.reference = reference
        return reference

    @api.model
    def _space(self, nbr, nbrspc=5):
        """Spaces ref by group of 5 digits.

        Example:
            AccountInvoice._space('123456789012345')
            '12 34567 89012 345'

        :param nbr: reference to group
        :type nbr: int

        :param nbrspc: group length
        :type nbrspc: int

        :return: grouped reference
        :rtype: str

        """
        return ''.join([' '[(i - 2) % nbrspc:] + c for i, c in enumerate(nbr)])

    @api.one
    @api.model
    def _compute_scan_line_list(self):
        """Generate a list containing all element of scan line

        the element are grouped by char or symbol

        This will allows the free placment of each element
        and enable a fine tuning of spacing

        :return: a list of sting representing the scan bar

        :rtype: list
        """
        line = []
        if not self.can_generate(self.move_line_id):
            return []
        amount = '01%.2f' % self.amount_total
        justified_amount = amount.replace('.', '').rjust(10, '0')
        line += [char for char in mod10r(justified_amount)]
        line.append('&gt;')
        line += [char for char in self.reference]
        line.append('+')
        line.append('&nbsp;')
        bank = self.move_line_id.invoice.partner_bank_id.get_account_number()
        account_components = bank.split('-')
        bank_identifier = "%s%s%s" % (
            account_components[0],
            account_components[1].rjust(6, '0'),
            account_components[2]
        )
        line += [car for car in bank_identifier]
        line.append('&gt;')
        return line

    @api.one
    @api.depends('move_line_id',
                 'move_line_id.debit',
                 'move_line_id.credit')
    def compute_scan_line(self):
        """Compute the payment slip scan line to be used
        by scanners

        :return: scan line
        :rtype: str
        """
        scan_line_list = self._compute_scan_line_list()
        self.scan_line = ''.join(scan_line_list)

    @api.one
    @api.depends('move_line_id',
                 'move_line_id.debit',
                 'move_line_id.credit')
    def compute_scan_line_div(self):
        """Compute the payment slip scan line HTML div to be used
        by scanners and inserted in report Qweb

        :return: scan line
        :rtype: str
        """
        company = self.env.user.company_id
        scan_line_list = self._compute_scan_line_list()
        ref_start_left = 1.5
        ref_coef_space = company.bvr_scan_line_letter_spacing or 2.55
        div = ['<div id="ocrbb">']
        for indice, block in enumerate(scan_line_list):
            div = '<div class="digitref" style="left:%smm">%s</div>'
            computed_div = div % (ref_start_left + (indice * ref_coef_space),
                                  block)
            div.append(computed_div)
        div.append('</div>')
        self.scan_line_div = ''.join(div)

    @api.model
    def get_slip_for_move_line(self, move_line):
        """Return pyment slip related to move

        :param move: `account.move.line` record
        :type move: :py:class:`openerp.models.Model`

        :return: payment slip recordset related to move line
        :rtype: :py:class:`openerp.models.Model`
        """
        return self.search(
            [('move_line_id', '=', move_line.id)]
        )

    @api.model
    def create_slip_from_move_line(self, move_line):
        """Generate `l10n_ch.payment_slip` from
        `account.move.line` recordset

        :param move_lines: Record of `account.move.line`
        :type move_line: :py:class:`openerp.models.Model`

        :return: Recordset of `l10n_ch.payment_slip`
        :rtype: :py:class:`openerp.models.Model`
        """
        return self.create({'move_line_id', move_line.id})

    @api.model
    def compute_pay_slips_from_move_lines(self, move_lines):
        """Get or generate `l10n_ch.payment_slip` from
        `account.move.line` recordset

        :param move_lines: Recordset of `account.move.line`
        :type move_lines: :py:class:`openerp.models.Model`

        :return: Recordset of `l10n_ch.payment_slip`
        :rtype: :py:class:`openerp.models.Model`

        """
        pay_slips = self.browse()
        for move in move_lines:
            if not self._can_generate(move):
                continue
            slip = self.get_slip_for_move_line(move)
            if not slip:
                slip = self.create_slip_from_move_line(move)
            if slip:
                pay_slips += slip
        return pay_slips

    @api.model
    def compute_slip_from_invoices(self, invoices):
        """Generate ```l10n_ch.payment_slip``` from
        ```account.invoice``` recordset

        :param move_lines: Recordset of `account.invoice`
        :type move_lines: :py:class:`openerp.models.Model`

        """
        for invoice in invoices:
            move_lines = invoice.get_payment_move_line()
            return self.compute_slip_from_move_lines(move_lines)

    @api.one
    def get_comm_partner(self):
        invoice = self.move_line_id.invoice
        if hasattr(invoice, 'commercial_partner_id'):
            return invoice.commercial_partner_id
        else:
            return invoice.partner_id

    @api.one
    def not_same_name(self):
        invoice = self.move_line_id.invoice
        if hasattr(invoice, 'commercial_partner_id'):
            return invoice.commercial_partner_id.id != invoice.partner_id.id
        else:
            return False

    @api.one
    def _validate(self):
        """Check if the payment slip is ready to be printed"""
        invoice = self.move_line_id.invoice
        if not invoice:
            raise exceptions.ValidationError(
                _('No invoice related to move line %') % self.move_line_id.ref
            )
        if not self._compile_check_bvr.match(
                invoice.partner_bank_id.get_account_number() or ''):
            raise exceptions.ValidationError(
                _('Your bank BVR number should be of the form 0X-XXX-X! '
                  'Please check your company '
                  'information for the invoice:\n%s') % (invoice.name)
            )
        return True

    @api.model
    def police_absolute_path(self):
        """Will get the ocrb police absolute path"""
        path = get_module_resource(
            'l10n_ch_payment_slip',
            'static',
            'scr',
            'font',
            'ocrbb.ttf',
        )
        return path

    @api.model
    def image_absolute_path(self, file_name):
        """Will get the ocrb police absolute path"""
        path = get_module_resource(
            'static',
            'scr',
            'img',
            file_name
        )
        return path

    @api.model
    def _get_text_font(self):
        return ImageFont.truetype(self.police_absolute_path,
                                  self._default_font_size)

    @api.model
    def _get_scan_line_text_font(self, company):
        return ImageFont.truetype(
            self.police_absolute_path,
            company.bvr_scan_line_font_size or self._default_font_size
        )

    @api.model
    def _draw_address(self, draw, font, invoice, initial_position, company):
        com_partner = self.get_comm_partner()
        x, y = initial_position
        x += company.bvr_add_horz
        y += company.bvr_add_vert
        draw.text((x, y), com_partner.name, font=font, fill=self._fill_color)
        width, height = font.getsize(com_partner.name)
        for line in com_partner.contact_address.split("\n"):
            width, height = font.getsize(line)
            draw.text((x, y),
                      com_partner.name,
                      font=font,
                      fill=self._fill_color)
            y += self._default_font_size

    @api.model
    def _draw_bank(self, draw, font, bank, initial_position, company):
        x, y = initial_position
        x += company.bvr_delta_horz
        y += company.bvr_delta_vert
        draw.text((x, y), bank, font=font, fill=self._fill_color)

    @api.model
    def _draw_amont(self, draw, font, amount, initial_position, company):
        x, y = initial_position
        x += company.bvr_delta_horz
        y += company.bvr_delta_vert
        indice = 0
        for car in amount:
            width, height = font.getsize(car)
            if indice:
                # some font type return non numerical
                x -= float(width) / 2.0
            draw.text((x, y), car, font=font, fill=(0, 0, 0, 0))
            x -= 11 + float(width) / 2.0
            indice += 1

    @api.model
    def _draw_scan_line(self, draw, font, initial_position, company):
        x, y = initial_position
        x += company.bvr_scan_line_horz
        y += company.bvr_scan_line_vert
        indice = 0
        for car in self._compute_scan_line_list():
            width, height = font.getsize(car)
            if indice:
                # some font type return non numerical
                x -= float(width) / 2.0
            draw.text((x, y), car, font=font, fill=(0, 0, 0, 0))
            x -= 11 + float(width) / 2.0
            indice += 1

    @api.model
    def _draw_hook(self, draw):
        pass

    @api.model
    @api.one
    def draw_payment_slip(self):
        """Generate the payment slip image"""
        company = self.env.user_id.company_id
        default_font = self._get_text_font()
        invoice = self.move_line_id.invoice
        scan_font = self._get_scan_line_text_font()
        bank_acc = self.move_line_id.invoice.partner_bank_id
        if company.bvr_background:
            base_image_path = self.image_absolute_path('bvr.png')
        else:
            base_image_path = self.image_absolute_path('white.png')
        base = Image.open(base_image_path).convert('RGBA')
        draw = ImageDraw.Draw(base)
        initial_position = (10, 43)
        self._draw_address(draw, default_font, invoice,
                           initial_position, company)
        initial_position = (10, 355)
        self._draw_address(draw, default_font, invoice,
                           initial_position, company)
        num_car, frac_car = ("%.2f" % self.amout_total).split('.')
        self._draw_amont(draw, default_font, num_car,
                         (214, 290), company)
        self._draw_amont(draw, default_font, num_car,
                         (304, 290), company)
        self._draw_amont(draw, default_font, num_car,
                         (560, 290), company)
        self._draw_amont(draw, default_font, num_car,
                         (650, 290), company)
        if invoice.partner_bank_id.print_account:
            self._draw_bank(draw, default_font,
                            bank_acc.get_account_number(),
                            (144, 240), company)
            self._draw_bank(draw, default_font,
                            bank_acc.get_account_number(),
                            (490, 240), company)
        self._draw_scan_line
        self._draw_hook(draw, scan_font, (1296, 475), company)
        with contextlib.closing(StringIO.StringIO()) as buff:
            base.save(buff, 'PNG', dpi=(144, 144))
            return buff.getvalue()

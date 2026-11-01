"""Business operations for booking, decisions, cancellation and reviews.

Keep these operations in one place so views/forms can reuse the same rules.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Appointment, AvailabilitySlot, Review, SpecialistProfile, User


class BookingError(Exception):
    """Base exception for expected booking workflow errors."""


class SlotUnavailableError(BookingError):
    """Raised when a time slot cannot be booked anymore."""


class InvalidAppointmentStateError(BookingError):
    """Raised when an operation is not allowed for the appointment's state."""


def request_appointment(*, client: User, slot_id: int, description: str = "") -> Appointment:
    """Create a pending appointment while protecting against double booking."""
    try:
        with transaction.atomic():
            slot = (
                AvailabilitySlot.objects.select_for_update()
                .select_related("specialist__user")
                .get(pk=slot_id)
            )
            if not slot.is_active or slot.start_at <= timezone.now():
                raise SlotUnavailableError("این بازه زمانی قابل رزرو نیست.")
            if slot.specialist.approval_status != SpecialistProfile.ApprovalStatus.APPROVED:
                raise SlotUnavailableError("متخصص هنوز تأیید نشده است.")
            if Appointment.objects.filter(
                slot=slot,
                status__in=Appointment.ACTIVE_STATUSES,
            ).exists():
                raise SlotUnavailableError("این زمان قبلاً رزرو شده است.")

            appointment = Appointment(client=client, slot=slot, description=description)
            appointment.full_clean()
            appointment.save()
            return appointment
    except AvailabilitySlot.DoesNotExist as exc:
        raise SlotUnavailableError("بازه زمانی موردنظر پیدا نشد.") from exc
    except IntegrityError as exc:
        # The conditional unique constraint is the final protection against races.
        raise SlotUnavailableError("این زمان هم‌زمان توسط کاربر دیگری رزرو شد.") from exc
    except ValidationError as exc:
        raise BookingError(str(exc)) from exc


def decide_appointment(
    *, appointment_id: int, specialist_user: User, approve: bool
) -> Appointment:
    """Approve or reject a pending request owned by the current specialist."""
    with transaction.atomic():
        try:
            appointment = (
                Appointment.objects.select_for_update()
                .select_related("slot__specialist__user")
                .get(pk=appointment_id)
            )
        except Appointment.DoesNotExist as exc:
            raise BookingError("درخواست نوبت پیدا نشد.") from exc

        if appointment.slot.specialist.user_id != specialist_user.pk:
            raise PermissionDenied("شما اجازه بررسی این درخواست را ندارید.")
        if appointment.status != Appointment.Status.PENDING:
            raise InvalidAppointmentStateError("این درخواست قبلاً بررسی شده است.")

        if approve:
            slot = AvailabilitySlot.objects.select_for_update().get(pk=appointment.slot_id)
            if not slot.is_active or slot.start_at <= timezone.now():
                raise SlotUnavailableError("زمان نوبت دیگر قابل تأیید نیست.")
            appointment.status = Appointment.Status.CONFIRMED
        else:
            appointment.status = Appointment.Status.REJECTED

        appointment.full_clean()
        appointment.save(update_fields=["status", "updated_at"])
        return appointment


def cancel_appointment(*, appointment_id: int, client: User) -> Appointment:
    """Cancel a customer's pending or confirmed appointment."""
    with transaction.atomic():
        try:
            appointment = Appointment.objects.select_for_update().get(pk=appointment_id)
        except Appointment.DoesNotExist as exc:
            raise BookingError("نوبت پیدا نشد.") from exc

        if appointment.client_id != client.pk:
            raise PermissionDenied("شما اجازه لغو این نوبت را ندارید.")
        if appointment.status not in Appointment.ACTIVE_STATUSES:
            raise InvalidAppointmentStateError("این نوبت دیگر قابل لغو نیست.")
        appointment.status = Appointment.Status.CANCELLED
        appointment.save(update_fields=["status", "updated_at"])
        return appointment


def reschedule_appointment(
    *, appointment_id: int, client: User, new_slot_id: int
) -> Appointment:
    """Move a pending/confirmed appointment to another slot of the same specialist.

    A rescheduled appointment returns to PENDING so the specialist can approve the
    new time. Updating the slot releases the old slot for another customer.
    """
    try:
        with transaction.atomic():
            try:
                appointment = (
                    Appointment.objects.select_for_update()
                    .select_related("slot__specialist")
                    .get(pk=appointment_id)
                )
            except Appointment.DoesNotExist as exc:
                raise BookingError("نوبت پیدا نشد.") from exc

            if appointment.client_id != client.pk:
                raise PermissionDenied("شما اجازه تغییر این نوبت را ندارید.")
            if appointment.status not in Appointment.ACTIVE_STATUSES:
                raise InvalidAppointmentStateError("این نوبت قابل تغییر نیست.")

            new_slot = (
                AvailabilitySlot.objects.select_for_update()
                .select_related("specialist")
                .get(pk=new_slot_id)
            )
            if new_slot.specialist_id != appointment.slot.specialist_id:
                raise BookingError("در این نسخه فقط می‌توان زمان دیگری از همان متخصص را انتخاب کرد.")
            if new_slot.pk == appointment.slot_id:
                raise BookingError("زمان جدید با زمان فعلی یکسان است.")
            if not new_slot.is_active or new_slot.start_at <= timezone.now():
                raise SlotUnavailableError("زمان انتخاب‌شده قابل رزرو نیست.")
            if new_slot.specialist.approval_status != SpecialistProfile.ApprovalStatus.APPROVED:
                raise SlotUnavailableError("متخصص در حال حاضر تأییدشده نیست.")
            if Appointment.objects.filter(
                slot=new_slot,
                status__in=Appointment.ACTIVE_STATUSES,
            ).exclude(pk=appointment.pk).exists():
                raise SlotUnavailableError("زمان جدید قبلاً رزرو شده است.")

            appointment.slot = new_slot
            appointment.status = Appointment.Status.PENDING
            appointment.full_clean()
            appointment.save(update_fields=["slot", "status", "updated_at"])
            return appointment
    except AvailabilitySlot.DoesNotExist as exc:
        raise SlotUnavailableError("بازه زمانی موردنظر پیدا نشد.") from exc
    except IntegrityError as exc:
        raise SlotUnavailableError("زمان جدید هم‌زمان توسط کاربر دیگری رزرو شد.") from exc
    except ValidationError as exc:
        raise BookingError(str(exc)) from exc


def mark_appointment_result(
    *, appointment_id: int, specialist_user: User, customer_attended: bool
) -> Appointment:
    """Record whether a confirmed appointment was completed or was a no-show."""
    with transaction.atomic():
        try:
            appointment = (
                Appointment.objects.select_for_update()
                .select_related("slot__specialist__user")
                .get(pk=appointment_id)
            )
        except Appointment.DoesNotExist as exc:
            raise BookingError("نوبت پیدا نشد.") from exc

        if appointment.slot.specialist.user_id != specialist_user.pk:
            raise PermissionDenied("شما اجازه ثبت نتیجه این جلسه را ندارید.")
        if appointment.status != Appointment.Status.CONFIRMED:
            raise InvalidAppointmentStateError("فقط نوبت تأییدشده قابل ثبت نتیجه است.")
        if appointment.slot.end_at > timezone.now():
            raise InvalidAppointmentStateError("تا پایان زمان جلسه امکان ثبت نتیجه وجود ندارد.")

        appointment.status = (
            Appointment.Status.COMPLETED
            if customer_attended
            else Appointment.Status.NO_SHOW
        )
        appointment.save(update_fields=["status", "updated_at"])
        return appointment


def submit_review(
    *, appointment_id: int, client: User, rating: int, comment: str = ""
) -> Review:
    """Create a review for the customer's completed appointment."""
    try:
        appointment = Appointment.objects.select_related("client").get(pk=appointment_id)
    except Appointment.DoesNotExist as exc:
        raise BookingError("نوبت پیدا نشد.") from exc

    if appointment.client_id != client.pk:
        raise PermissionDenied("شما اجازه ثبت بازخورد برای این نوبت را ندارید.")
    review = Review(appointment=appointment, rating=rating, comment=comment)
    try:
        review.full_clean()
        review.save()
    except ValidationError as exc:
        raise BookingError(str(exc)) from exc
    except IntegrityError as exc:
        raise BookingError("برای این نوبت قبلاً بازخورد ثبت شده است.") from exc
    return review

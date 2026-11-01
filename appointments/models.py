"""Database models for the online appointment booking system."""
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class User(AbstractUser):
    """A system account. Admins are handled by Django's is_staff/is_superuser flags."""

    class Role(models.TextChoices):
        CUSTOMER = "customer", "کاربر عادی"
        SPECIALIST = "specialist", "متخصص"

    email = models.EmailField("ایمیل", unique=True)
    role = models.CharField(
        "نقش کاربر",
        max_length=20,
        choices=Role.choices,
        default=Role.CUSTOMER,
    )

    class Meta:
        verbose_name = "کاربر"
        verbose_name_plural = "کاربران"
        ordering = ["username"]

    def __str__(self):
        return self.get_full_name() or self.username


class SpecialistProfile(models.Model):
    """Professional information and admin approval state for a specialist."""

    class ApprovalStatus(models.TextChoices):
        PENDING = "pending", "در انتظار بررسی"
        APPROVED = "approved", "تأییدشده"
        REJECTED = "rejected", "ردشده"

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="specialist_profile",
        verbose_name="حساب کاربری",
    )
    specialty = models.CharField("حوزه تخصص", max_length=120)
    bio = models.TextField("معرفی و توضیحات", blank=True)
    experience_years = models.PositiveSmallIntegerField("سابقه کاری (سال)", default=0)
    approval_status = models.CharField(
        "وضعیت تأیید",
        max_length=12,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    rejection_reason = models.TextField("دلیل رد درخواست", blank=True)
    created_at = models.DateTimeField("تاریخ ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین به‌روزرسانی", auto_now=True)

    class Meta:
        verbose_name = "پروفایل متخصص"
        verbose_name_plural = "پروفایل متخصصان"
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        errors = {}
        if self.user_id and self.user.role != User.Role.SPECIALIST:
            errors["user"] = "این پروفایل فقط می‌تواند متعلق به حسابی با نقش متخصص باشد."
        if self.approval_status == self.ApprovalStatus.REJECTED and not self.rejection_reason.strip():
            errors["rejection_reason"] = "برای رد پروفایل، ثبت دلیل الزامی است."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.user} - {self.specialty}"


class AvailabilitySlot(models.Model):
    """A time interval that a specialist makes available for appointments."""

    specialist = models.ForeignKey(
        SpecialistProfile,
        on_delete=models.CASCADE,
        related_name="availability_slots",
        verbose_name="متخصص",
    )
    start_at = models.DateTimeField("زمان شروع")
    end_at = models.DateTimeField("زمان پایان")
    is_active = models.BooleanField("فعال", default=True)
    created_at = models.DateTimeField("تاریخ ایجاد", auto_now_add=True)

    class Meta:
        verbose_name = "بازه زمانی"
        verbose_name_plural = "بازه‌های زمانی"
        ordering = ["start_at"]
        indexes = [models.Index(fields=["specialist", "start_at"]) ]

    def clean(self):
        super().clean()
        errors = {}
        if self.start_at and self.end_at:
            if self.end_at <= self.start_at:
                errors["end_at"] = "زمان پایان باید بعد از زمان شروع باشد."
            if self.is_active and self.start_at <= timezone.now():
                errors["start_at"] = "زمان شروع بازه فعال باید در آینده باشد."
        if self.specialist_id and self.is_active:
            profile = self.specialist
            if profile.approval_status != SpecialistProfile.ApprovalStatus.APPROVED:
                errors["specialist"] = "فقط متخصص تأییدشده می‌تواند زمان فعال تعریف کند."
            if self.start_at and self.end_at and self.end_at > self.start_at:
                overlaps = AvailabilitySlot.objects.filter(
                    specialist_id=self.specialist_id,
                    is_active=True,
                    start_at__lt=self.end_at,
                    end_at__gt=self.start_at,
                )
                if self.pk:
                    overlaps = overlaps.exclude(pk=self.pk)
                if overlaps.exists():
                    errors["start_at"] = "این بازه با یکی از زمان‌های فعال متخصص تداخل دارد."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.specialist} | {self.start_at:%Y-%m-%d %H:%M}"


class Appointment(models.Model):
    """A customer's request for one specialist availability slot."""

    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار تأیید"
        CONFIRMED = "confirmed", "تأییدشده"
        REJECTED = "rejected", "ردشده"
        CANCELLED = "cancelled", "لغوشده"
        COMPLETED = "completed", "انجام‌شده"
        NO_SHOW = "no_show", "عدم حضور"

    ACTIVE_STATUSES = (Status.PENDING, Status.CONFIRMED)

    client = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name="کاربر درخواست‌دهنده",
    )
    slot = models.ForeignKey(
        AvailabilitySlot,
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name="بازه زمانی",
    )
    status = models.CharField(
        "وضعیت نوبت",
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    description = models.TextField("توضیحات کاربر", blank=True)
    created_at = models.DateTimeField("تاریخ ثبت", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین به‌روزرسانی", auto_now=True)

    class Meta:
        verbose_name = "نوبت"
        verbose_name_plural = "نوبت‌ها"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["slot"],
                condition=Q(status__in=["pending", "confirmed"]),
                name="uniq_active_appointment_per_slot",
            ),
        ]
        indexes = [models.Index(fields=["client", "status"]) ]

    def clean(self):
        super().clean()
        errors = {}
        if self.client_id and self.client.role != User.Role.CUSTOMER:
            errors["client"] = "ثبت درخواست نوبت فقط برای کاربران عادی مجاز است."
        if self.slot_id:
            slot = self.slot
            if self.client_id and slot.specialist.user_id == self.client_id:
                errors["client"] = "متخصص نمی‌تواند برای زمان کاری خودش نوبت ثبت کند."

            # Historical states (rejected/cancelled/completed/no-show) must remain
            # valid even after the slot has passed or has been deactivated.
            if self.status in self.ACTIVE_STATUSES:
                if not slot.is_active:
                    errors["slot"] = "این بازه زمانی دیگر فعال نیست."
                if slot.start_at <= timezone.now():
                    errors["slot"] = "برای زمان‌های گذشته یا شروع‌شده نمی‌توان نوبت فعال داشت."
                if slot.specialist.approval_status != SpecialistProfile.ApprovalStatus.APPROVED:
                    errors["slot"] = "متخصص هنوز توسط مدیر تأیید نشده است."

                conflicts = Appointment.objects.filter(
                    slot_id=self.slot_id,
                    status__in=self.ACTIVE_STATUSES,
                )
                if self.pk:
                    conflicts = conflicts.exclude(pk=self.pk)
                if conflicts.exists():
                    errors["slot"] = "این زمان قبلاً توسط درخواست فعال دیگری رزرو شده است."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"نوبت #{self.pk or 'جدید'} - {self.client}"


class Review(models.Model):
    """A single rating and comment submitted for a completed appointment."""

    appointment = models.OneToOneField(
        Appointment,
        on_delete=models.CASCADE,
        related_name="review",
        verbose_name="نوبت",
    )
    rating = models.PositiveSmallIntegerField(
        "امتیاز",
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    comment = models.TextField("نظر کاربر", blank=True)
    created_at = models.DateTimeField("تاریخ ثبت", auto_now_add=True)

    class Meta:
        verbose_name = "بازخورد"
        verbose_name_plural = "بازخوردها"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__gte=1) & Q(rating__lte=5),
                name="review_rating_between_1_and_5",
            ),
        ]

    def clean(self):
        super().clean()
        if self.appointment_id and self.appointment.status != Appointment.Status.COMPLETED:
            raise ValidationError({"appointment": "فقط برای نوبت انجام‌شده می‌توان نظر ثبت کرد."})

    def __str__(self):
        return f"بازخورد نوبت #{self.appointment_id} ({self.rating}/5)"

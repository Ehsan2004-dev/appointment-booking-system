from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Appointment, AvailabilitySlot, Review, SpecialistProfile, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active")
    fieldsets = BaseUserAdmin.fieldsets + (("نقش در سامانه", {"fields": ("role",)}),)
    add_fieldsets = BaseUserAdmin.add_fieldsets + (("اطلاعات تکمیلی", {"fields": ("email", "role")}),)


@admin.register(SpecialistProfile)
class SpecialistProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "specialty", "experience_years", "approval_status", "created_at")
    list_filter = ("approval_status", "specialty")
    search_fields = ("user__username", "user__first_name", "user__last_name", "specialty")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AvailabilitySlot)
class AvailabilitySlotAdmin(admin.ModelAdmin):
    list_display = ("specialist", "start_at", "end_at", "is_active")
    list_filter = ("is_active", "specialist__approval_status")
    date_hierarchy = "start_at"


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("id", "client", "slot", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("client__username", "client__first_name", "client__last_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("appointment", "rating", "created_at")
    list_filter = ("rating", "created_at")
    readonly_fields = ("created_at",)

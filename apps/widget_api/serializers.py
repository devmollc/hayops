from rest_framework import serializers

from apps.tickets.models import Comment, Ticket


class WidgetTicketListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ticket
        fields = [
            "id",
            "reference_number",
            "subject",
            "status",
            "priority",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WidgetTicketCreateSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=300)
    body = serializers.CharField()
    page_url = serializers.CharField(required=False, allow_blank=True)
    user_agent = serializers.CharField(required=False, allow_blank=True)
    screenshot = serializers.FileField(required=False)
    submitter_email = serializers.EmailField(required=False)
    submitter_name = serializers.CharField(required=False, max_length=255, allow_blank=True)
    context = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        request = self.context.get("request")
        if request and request.user.is_anonymous and not attrs.get("submitter_email"):
            raise serializers.ValidationError(
                {"submitter_email": "Email is required for anonymous submissions."}
            )
        return attrs


class WidgetCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Comment
        fields = ["id", "body", "author_email", "created_at"]
        read_only_fields = ["id", "author_email", "created_at"]


class WidgetTicketDetailSerializer(serializers.ModelSerializer):
    comments = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id",
            "reference_number",
            "subject",
            "body",
            "status",
            "priority",
            "created_at",
            "comments",
        ]
        read_only_fields = fields

    def get_comments(self, obj):
        public_comments = obj.comments.filter(
            comment_type=Comment.CommentType.PUBLIC
        )
        return WidgetCommentSerializer(public_comments, many=True).data

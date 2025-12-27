resource "aws_sqs_queue" "orchestrator_dlq" {
  name = "${var.project_name}-orchestrator-dlq"
  message_retention_seconds = 1209600  # 14 days
}

resource "aws_sqs_queue" "orchestrator" {
  name                       = "${var.project_name}-orchestrator-queue"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 1209600
  receive_wait_time_seconds  = 20
  
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.orchestrator_dlq.arn
    maxReceiveCount     = 3
  })
}

output "queue_url" {
  value = aws_sqs_queue.orchestrator.url
}

output "queue_arn" {
  value = aws_sqs_queue.orchestrator.arn
}

output "dlq_url" {
  value = aws_sqs_queue.orchestrator_dlq.url
}

variable "project_name" {}
variable "environment" {}
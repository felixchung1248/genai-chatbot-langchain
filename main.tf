resource "helm_release" "langchain-chatbot" {
  name       = "langchain-chatbot"
  chart      = "/chart"
  namespace  = "langchain-chatbot-ns"
  create_namespace = true
}
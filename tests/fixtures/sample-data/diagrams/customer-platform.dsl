workspace "Customer Platform" "Sample Structurizr DSL fixture" {
  model {
    customer = person "Customer"
    system = softwareSystem "Customer Platform"
    customer -> system "Uses"
  }
  views {
    systemContext system {
      include *
      autolayout lr
    }
  }
}

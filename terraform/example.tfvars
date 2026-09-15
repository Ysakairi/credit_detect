project_id = "your-gcp-project-id"

# Dataform Git は terraform.tfvars に書かない。main.tf の既定を使う。
#   url    = https://github.com/Ysakairi/credit_detect_dataform.git
#   branch = main
#   token  = projects/<project_id>/secrets/dataform-github-token/versions/latest
# apply 前にシークレット dataform-github-token を作る（README ⑥）。
# 上書きするときだけ:
# dataform_git_url             = "https://github.com/Ysakairi/credit_detect_dataform.git"
# dataform_github_token_secret = "projects/your-gcp-project-id/secrets/dataform-github-token/versions/latest"

#! /usr/bin/expect

# Wait for finish forever after send password
set password 123456
# Put your jarsigner command here
spawn /usr/bin/rsync -aAXvP --exclude=jorek.*.vtk marconi:/xxxxx/ xxxxx/
expect "Enter passphrase for key '/u/hazh/.ssh/my_key': "
send "$password\r"
# log_user 1
# Wait for the rsync operation to complete
# expect eof
# exit "$exit_code"
# Set timeout to -1 (infinite)
set timeout -1

# Wait indefinitely
expect {
    eof { }
}
